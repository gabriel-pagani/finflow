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

from ..models import Message, Role
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
    """A conversa como o modelo a relê: os itens de cada turno, em ordem."""
    items = []
    for message in recent(conversation):
        items.extend(message.items)
    return items


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
            raise ModelError(f'Resposta terminou em {event.type}.')

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


def converse(conversation, user, text):
    """Uma mensagem do usuário, do envio até a resposta final.

    Devolve eventos: 'delta' com o texto saindo, 'tool' quando uma consulta
    começa, 'pending' quando um lançamento ficou à espera de confirmação,
    'error' e 'done'.
    """
    today = timezone.localdate()

    Message.objects.create(
        conversation=conversation,
        role=Role.USER,
        content=text,
        items=[{'role': 'user', 'content': text}],
    )

    # O título é a primeira coisa que o usuário disse. Pedir um ao modelo custaria
    # uma chamada inteira para nomear algo que ele reconhece pelo próprio texto.
    if not conversation.title:
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
