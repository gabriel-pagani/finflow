import json
import logging

from django.conf import settings
from django.utils import timezone
from openai import OpenAI, OpenAIError

from . import attachments, commands
from .models import MAX_MESSAGE, AttachmentKind, Message, Role
from .prompt import system_prompt
from .tools import TOOLS, run


logger = logging.getLogger(__name__)

# Teto de idas e voltas com o modelo por mensagem: um modelo em laço, repetindo a
# consulta de que não gostou, para aqui em vez de gastar indefinidamente.
MAX_ROUNDS = 8

HISTORY_LIMIT = 40

# Os tetos de tamanho do que sobe para a API, em caracteres — a medida que existe
# aqui, e que dá uns quatro caracteres por token. O limite de mensagens por dia
# conta envios, não o que cada um consome, e sem estes tetos uma mensagem só
# ("liste tudo, de 200 em 200") custa mais do que um dia inteiro de conversa: a
# conversa vai inteira em cada uma das rodadas, e uma rodada pode disparar várias
# chamadas de uma vez.
#
# MAX_TOOL_OUTPUT corta a consulta larga demais para entrar na conversa;
# TOOL_BUDGET é o quanto as consultas de uma mensagem podem somar; HISTORY_BUDGET
# é o quanto das mensagens anteriores volta junto.
MAX_TOOL_OUTPUT = 30_000
TOOL_BUDGET = 120_000
HISTORY_BUDGET = 60_000

# Trava de segurança para uma resposta que não termina; a resposta normal é muito
# menor, e o raciocínio do modelo cabe com folga.
MAX_OUTPUT_TOKENS = 8_000

REPEATED = {
    'ok': False,
    'error': 'Chamada idêntica a uma que já falhou nesta mensagem; não foi executada de novo. Corrija o que o erro apontou ou explique ao usuário o que falta.',
}

EXHAUSTED = {
    'ok': False,
    'error': 'As consultas desta mensagem já ocuparam o espaço que cabe na conversa, e esta não foi executada. Responda com o que já tem ou peça ao usuário para dividir o pedido.',
}

OVERSIZED = {
    'ok': False,
    'error': 'O retorno é grande demais para caber na conversa e foi descartado. Estreite o recorte: período mais curto, menos transações por vez, menos eixos em group_by.',
}

# Quantas mensagens com foto voltam com a foto. As anteriores viram um marcador:
# reenviar todo comprovante a cada pergunta encareceria a conversa inteira, e o
# que se pergunta logo depois de uma foto é sobre ela.
IMAGE_MEMORY = 2

# Sem vocabulário, "Pix" vira "pics" e "fatura" vira "fartura".
TRANSCRIPTION_HINT = (
    'Fala em português do Brasil sobre finanças pessoais: reais, Pix, boleto, débito, '
    'crédito, fatura, parcelas, cartão, transferência, salário, mercado, farmácia.'
)

GENERIC_ERROR = 'Não consegui concluir agora. Tente de novo em instantes.'


class ModelError(Exception):
    pass


def client():
    return OpenAI(api_key=settings.OPENAI_API_KEY)


# Do mais novo para o mais velho. A rodada em curso vai inteira, custe o que
# custar: ela começa na fala do usuário e cortá-la deixaria chamada sem resposta,
# que a API recusa. O que veio antes entra enquanto couber no teto, contado pelo
# texto de cada turno — o raciocínio cifrado ocupa muito caractere e poucos
# tokens, e contá-lo cortaria a conversa cedo demais.
def history(conversation):
    window, budget, turn = [], HISTORY_BUDGET, True

    for message in conversation.messages.order_by('-created_at', '-id')[:HISTORY_LIMIT]:
        if not turn:
            budget -= len(message.content)
            if budget < 0:
                break
        window.append(message)
        turn = turn and message.role != Role.USER

    window.reverse()

    # O corte pode começar numa resposta de ferramenta cuja chamada ficou fora da
    # janela, e a API recusa a conversa inteira nesse caso.
    while window and window[0].role == Role.TOOL:
        window.pop(0)

    with_image = [message.pk for message in window if attachments.has_image(message.items)]
    live = set(with_image[-IMAGE_MEMORY:])

    return [item for message in window for item in attachments.resolve(message.items, conversation.user, inline=message.pk in live)]


def transcribe(upload):
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


def collect(stream):
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

    # Os itens vêm do evento final, e não dos deltas, porque só ele traz o
    # raciocínio cifrado que precisa voltar intacto na rodada seguinte.
    yield None, (''.join(text), [item.model_dump(exclude_none=True) for item in final.output])


def arguments_of(call):
    try:
        parsed = json.loads(call.get('arguments') or '{}')
    except json.JSONDecodeError as error:
        return None, f'Os argumentos não são JSON válido: {error}.'
    if not isinstance(parsed, dict):
        return None, 'Os argumentos precisam ser um objeto JSON.'
    return parsed, None


def execute(call, user, today, conversation):
    arguments, error = arguments_of(call)
    if error:
        return {'ok': False, 'error': error}, None

    try:
        return run(call.get('name', ''), arguments, user=user, today=today, conversation=conversation)
    except Exception:
        logger.exception('Ferramenta %r falhou na conversa %s.', call.get('name'), conversation.pk)
        return {'ok': False, 'error': 'A ferramenta falhou. Avise que não foi possível concluir agora.'}, None


def converse(conversation, user, text, upload=None, command=None):
    today = timezone.localdate()

    if upload is not None and upload.kind == AttachmentKind.AUDIO:
        try:
            spoken = transcribe(upload)
        except (OpenAIError, ModelError):
            logger.exception('Áudio da conversa %s não pôde ser transcrito.', conversation.pk)
            yield {'type': 'error', 'message': 'Não consegui entender o áudio. Tente gravar de novo.'}
            return

        # O que foi digitado e o que foi dito são a mesma fala, e vão num turno só.
        text = '\n'.join(part for part in (text, spoken) if part)

        # O teto de caracteres vale para a fala também: sem isto, um áudio longo
        # entra como mensagem de tamanho nenhum e volta para a API a cada rodada.
        if len(text) > MAX_MESSAGE:
            yield {'type': 'error', 'message': f'O áudio é longo demais: a transcrição passou de {MAX_MESSAGE} caracteres. Grave um mais curto.'}
            return

        yield {'type': 'transcript', 'text': text}

    # O chat mostra o /comando que foi digitado; só o modelo lê as instruções.
    message = Message.objects.create(conversation=conversation, role=Role.USER, content=text)
    attachment = attachments.attach(message, upload) if upload is not None else None
    prompt = commands.expand(command, text) if command is not None else text
    message.items = [attachments.user_item(prompt, attachment)]
    message.save(update_fields=['items'])

    # Chamada idêntica a uma que já falhou nesta mensagem daria o mesmo erro, e
    # repeti-la só consome as rodadas até o teto.
    failed = set()
    # Quanto as consultas desta mensagem já ocuparam, somando as rodadas.
    spent = 0

    for _ in range(MAX_ROUNDS):
        try:
            stream = client().responses.create(
                model=settings.OPENAI_MODEL,
                instructions=system_prompt(user, today),
                input=history(conversation),
                tools=TOOLS,
                stream=True,
                store=False,
                include=['reasoning.encrypted_content'],
                max_output_tokens=MAX_OUTPUT_TOKENS,
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

        Message.objects.create(conversation=conversation, role=Role.ASSISTANT, content=content, items=output)

        calls = [item for item in output if item.get('type') == 'function_call']
        if not calls:
            yield {'type': 'done'}
            return

        for call in calls:
            yield {'type': 'tool', 'name': call.get('name', '')}

            signature = (call.get('name'), call.get('arguments'))
            proposal = None

            if spent >= TOOL_BUDGET:
                payload = EXHAUSTED
            elif signature in failed:
                payload = REPEATED
            else:
                payload, proposal = execute(call, user, today, conversation)
                if payload.get('ok') is False:
                    failed.add(signature)

            result = json.dumps(payload, ensure_ascii=False, default=str)

            # O que não cabe fica de fora inteiro: guardado, ele voltaria à API
            # em cada rodada seguinte e empurraria o resto da conversa para fora.
            if len(result) > MAX_TOOL_OUTPUT:
                payload, proposal = OVERSIZED, None
                result = json.dumps(payload, ensure_ascii=False)
                failed.add(signature)

            spent += len(result)
            Message.objects.create(
                conversation=conversation,
                role=Role.TOOL,
                content=result,
                items=[{'type': 'function_call_output', 'call_id': call['call_id'], 'output': result}],
            )

            if proposal is not None:
                yield {'type': 'proposal', 'id': proposal.pk, 'summary': proposal.summary, 'state': proposal.state}

    logger.warning('Conversa %s excedeu %s rodadas de ferramenta.', conversation.pk, MAX_ROUNDS)
    yield {'type': 'error', 'message': 'Não consegui fechar uma resposta para isso. Tente perguntar de outro jeito.'}
