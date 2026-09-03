"""O laço de conversa com o modelo: manda, ouve, executa ferramenta, repete.

Este é o pedaço que o n8n fazia por conta própria, e é a razão de ele existir. A
diferença é que aqui o laço roda dentro da requisição, com o usuário da sessão em
mãos, então cada ferramenta que ele despacha já sabe de quem é o dinheiro.

Usa a Responses API, e não a de chat, porque o modelo deste sistema raciocina
antes de responder — e a API de chat recusa ferramentas e raciocínio na mesma
chamada. Escolher a de chat significaria desligar o raciocínio justamente onde
ele rende: escolher o recorte certo de uma pergunta vaga, conferir se a conta
aceita a combinação, não somar dois recortes que contam o mesmo dinheiro.

O que sai daqui é uma sequência de eventos, e não uma resposta pronta. Uma
pergunta que precise de duas consultas leva dezenas de segundos até a primeira
palavra, e uma tela parada por dezenas de segundos parece travada — então o que
vai para o navegador é o texto conforme ele sai, e o aviso de qual consulta está
em curso enquanto não há texto nenhum.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone
from openai import OpenAI, OpenAIError

from ..models import Attachment, Message, Role
from . import attachments
from .prompt import system_prompt
from .tools import TOOLS, run


logger = logging.getLogger(__name__)


# Teto de idas e voltas com o modelo numa única mensagem do usuário. Uma pergunta
# honesta se resolve em duas ou três; o teto existe para um modelo que entrou em
# laço — pedindo a mesma consulta de novo porque não gostou da resposta — parar de
# gastar dinheiro e tempo às custas de alguém que só quer saber o saldo.
MAX_ROUNDS = 6

# Teto de turnos do histórico reenviados ao modelo. Uma conversa não termina
# sozinha: sem teto, cada pergunta reenviaria todas as anteriores, e a conta de
# tokens de quem usa o chat o dia inteiro cresceria pelo quadrado do uso — até a
# conversa simplesmente não caber no contexto e parar de responder.
HISTORY_LIMIT = 40

# Quantos turnos com foto continuam com a foto anexada. O arquivo fica guardado
# para sempre, e a miniatura continua no chat; o que expira é a presença da
# imagem no contexto. Reenviar toda nota fiscal já fotografada a cada mensagem
# faria um "quanto sobrou este mês?" custar as fotos da conversa inteira — e o
# que se pergunta logo depois de uma foto é sobre o lançamento, não sobre ela.
# Dois turnos cobrem o "olha de novo, o total está errado".
IMAGE_MEMORY = 2

# Vocabulário para a transcrição não escorregar no que este sistema mais ouve.
# Sem isto, "Pix" vira "pics" e "fatura" vira "fartura" — e o número é o que
# menos pode sair errado numa fala que vai virar lançamento.
TRANSCRIPTION_HINT = (
    'Fala em português do Brasil sobre finanças pessoais: reais, Pix, boleto, débito, '
    'crédito, fatura, parcelas, cartão, transferência, salário, mercado, farmácia.'
)

# Frase para o usuário quando a falha não é dele e não há o que ele resolva. O
# detalhe técnico vai para o log, não para a tela: é a mesma regra que o prompt
# impõe ao modelo, e não faria sentido a aplicação furá-la.
GENERIC_ERROR = 'Não consegui concluir agora. Tente de novo em instantes.'


class ModelError(Exception):
    """O modelo terminou sem uma resposta utilizável."""


def client():
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def recent(conversation):
    """Os últimos turnos da conversa, sem resposta de ferramenta órfã.

    O corte é por quantidade, e a última fatia pode começar no meio de uma
    rodada — com a resposta da ferramenta dentro da janela e a chamada que a
    pediu fora dela. A API recusa a conversa inteira quando isso acontece, então
    o começo é aparado até o primeiro turno que não seja resposta de ferramenta:
    perde-se o resultado de uma consulta antiga, não a conversa.
    """
    window = list(conversation.messages.reverse()[:HISTORY_LIMIT])[::-1]

    while window and window[0].role == Role.TOOL:
        window.pop(0)

    return window


def history(conversation):
    """A conversa como o modelo a relê: os itens de cada turno, em ordem.

    A foto é a exceção. No turno guardado fica só a referência ao arquivo, e é
    aqui que ela vira imagem de novo — nas mais recentes. As antigas viram uma
    linha de texto dizendo que houve uma foto ali, o que basta para a resposta
    que o modelo deu sobre ela continuar tendo antecedente.
    """
    messages = recent(conversation)

    with_image = [message.pk for message in messages if attachments.has_image(message.items)]
    live = set(with_image[-IMAGE_MEMORY:])

    items = []
    for message in messages:
        items.extend(attachments.resolve(message.items, inline=message.pk in live))
    return items


def transcribe(upload):
    """O áudio virado texto, para entrar na conversa como qualquer mensagem.

    O modelo do chat não escuta, lê. Transcrever na porta de entrada mantém o
    laço inteiro em texto: o histórico continua legível, o reenvio a cada rodada
    continua barato, e o que foi ditado pode ser reconferido depois contra o
    áudio, que fica guardado.
    """
    result = client().audio.transcriptions.create(
        model=settings.OPENAI_TRANSCRIBE_MODEL,
        file=(upload.name, upload.data, upload.mime),
        language='pt',
        prompt=TRANSCRIPTION_HINT,
    )

    text = (result.text or '').strip()

    if not text:
        raise ModelError('A transcrição voltou vazia.')

    return text


def detail(event):
    """O que a API disse sobre o fim ruim, em uma linha, para o log.

    Sem isto o log guarda só o tipo do evento, e "terminou em
    response.incomplete" não separa um teto de saída estourado de um filtro de
    conteúdo — duas causas que pedem correções diferentes. O detalhe continua
    sem chegar ao usuário: quem o lê é quem abre o log.
    """
    response = getattr(event, 'response', None)
    error = getattr(response, 'error', None) or getattr(event, 'error', None)
    incomplete = getattr(response, 'incomplete_details', None)

    for part in (error, incomplete):
        if part is not None:
            reason = getattr(part, 'reason', None) or getattr(part, 'message', None)
            return str(reason or part)

    return str(getattr(event, 'message', None) or 'sem detalhe')


def collect(stream):
    """Consome o stream, emite os deltas e devolve (texto, itens de saída).

    Os itens finais vêm do evento de conclusão, e não montados a partir dos
    deltas: eles carregam o raciocínio cifrado, que precisa voltar intacto na
    rodada seguinte e não passa pelos deltas de texto.
    """
    text = []
    final = None

    for event in stream:
        if event.type == 'response.output_text.delta':
            text.append(event.delta)
            yield {'type': 'delta', 'text': event.delta}, None
        elif event.type == 'response.completed':
            final = event.response
        elif event.type in ('response.failed', 'response.incomplete', 'error'):
            raise ModelError(f'Resposta terminou em {event.type}: {detail(event)}.')

    if final is None:
        raise ModelError('O stream acabou sem resposta concluída.')

    yield None, (''.join(text), [item.model_dump(exclude_none=True) for item in final.output])


def arguments_of(call):
    """Os argumentos da chamada, ou um erro que o modelo consegue ler.

    JSON quebrado acontece, e não deve derrubar a conversa: o modelo recebe de
    volta o que houve e refaz a chamada, que é o que ele faria com qualquer
    outro erro de parâmetro.
    """
    raw = call.get('arguments') or '{}'
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, f'Os argumentos não são JSON válido: {error}. Refaça a chamada.'
    if not isinstance(parsed, dict):
        return None, 'Os argumentos precisam ser um objeto JSON.'
    return parsed, None


def converse(conversation, user, text, upload=None):
    """Uma mensagem do usuário, do envio até a resposta final.

    Devolve eventos: 'transcript' com o que o áudio dizia, 'delta' com o texto
    saindo, 'tool' quando uma consulta começa, 'pending' quando um lançamento
    ficou à espera de confirmação, 'error' e 'done'.
    """
    today = timezone.localdate()

    if upload is not None and upload.kind == Attachment.Kind.AUDIO:
        try:
            spoken = transcribe(upload)
        except (OpenAIError, ModelError):
            logger.exception('Áudio da conversa %s não pôde ser transcrito.', conversation.pk)
            yield {'type': 'error', 'message': 'Não consegui entender o áudio. Tente gravar de novo.'}
            return

        # O que foi ditado é a mensagem. Se a pessoa também digitou, as duas
        # coisas são a mesma fala e vão juntas: separá-las em dois turnos faria o
        # modelo responder à metade que chegasse primeiro.
        text = '\n'.join(part for part in (text, spoken) if part)
        yield {'type': 'transcript', 'text': text}

    message = Message.objects.create(
        conversation=conversation,
        role=Role.USER,
        content=text,
        items=[],
    )

    # Nesta ordem porque cada um depende do anterior: o caminho do arquivo sai do
    # dono, que se descobre pela mensagem, e o item guardado carrega o número da
    # linha do anexo, que só existe depois de gravado.
    attachment = attachments.attach(message, upload) if upload is not None else None
    message.items = [attachments.user_item(text, attachment)]
    message.save(update_fields=['items'])

    # O título é a primeira coisa que o usuário disse. Pedir um ao modelo custaria
    # uma chamada inteira para nomear algo que ele reconhece pelo próprio texto.
    # Uma foto sem legenda não nomeia nada: o título espera o primeiro texto.
    if not conversation.title and text:
        conversation.title = text[:120]
        conversation.save(update_fields=['title', 'updated'])

    for _ in range(MAX_ROUNDS):
        try:
            stream = client().responses.create(
                model=settings.OPENAI_MODEL,
                instructions=system_prompt(user, today),
                input=history(conversation),
                tools=TOOLS,
                stream=True,
                # A conversa é guardada aqui, não lá: o histórico já está no
                # banco, com dono, e some junto quando o usuário limpa o chat.
                store=False,
                # Sem isto o raciocínio não volta entre uma rodada de ferramenta
                # e a seguinte, e o modelo reconsulta o que acabou de consultar.
                include=['reasoning.encrypted_content'],
            )

            content, output = '', []
            for event, result in collect(stream):
                if event:
                    yield event
                else:
                    content, output = result
        except (OpenAIError, ModelError):
            logger.exception('Falha na conversa %s com o modelo.', conversation.pk)
            yield {'type': 'error', 'message': GENERIC_ERROR}
            return

        Message.objects.create(
            conversation=conversation,
            role=Role.ASSISTANT,
            content=content,
            items=output,
        )

        calls = [item for item in output if item.get('type') == 'function_call']

        if not calls:
            yield {'type': 'done'}
            return

        for call in calls:
            name = call.get('name', '')
            yield {'type': 'tool', 'name': name}

            parsed, error = arguments_of(call)
            if error:
                payload, pending = {'ok': False, 'error': 'argumentos_invalidos', 'message': error}, None
            else:
                try:
                    payload, pending = run(name, parsed, user=user, today=today, conversation=conversation)
                except Exception:
                    # Uma falha inesperada da ferramenta volta para o modelo como
                    # resposta, e não sobe: ele tem como contornar ou avisar o
                    # usuário, e derrubar a conversa não daria nenhuma das duas.
                    logger.exception('Ferramenta %r falhou na conversa %s.', name, conversation.pk)
                    payload, pending = {'ok': False, 'error': 'falha_interna', 'message': 'A ferramenta falhou. Avise que não foi possível concluir agora.'}, None

            result = json.dumps(payload, default=str, ensure_ascii=False)

            Message.objects.create(
                conversation=conversation,
                role=Role.TOOL,
                content=result,
                items=[{
                    'type': 'function_call_output',
                    'call_id': call['call_id'],
                    'output': result,
                }],
            )

            if pending is not None:
                yield {'type': 'pending', 'id': pending.pk, 'summary': pending.summary}

    # Estourou o teto: o modelo continua pedindo ferramenta sem chegar a uma
    # resposta. O usuário precisa saber que a pergunta ficou sem resposta, e não
    # ficar olhando um chat que simplesmente parou.
    logger.warning('Conversa %s excedeu %s rodadas de ferramenta.', conversation.pk, MAX_ROUNDS)
    yield {'type': 'error', 'message': 'Não consegui fechar uma resposta para isso. Tente perguntar de outro jeito.'}
