import json
from decimal import Decimal

from django.urls import reverse

from assistant import client as client_module
from assistant.client import HISTORY_LIMIT, MAX_ROUNDS, converse, history
from assistant.models import Conversation, Message, Role


def eventos(response):
    body = b''.join(response.streaming_content).decode()
    return [json.loads(chunk[len('data: '):]) for chunk in body.split('\n\n') if chunk.startswith('data: ')]


def test_consulta_volta_para_o_modelo_antes_da_resposta(allowed, user, make_transaction, fake_openai):
    make_transaction(value=Decimal('42.00')).save()
    fake_openai.turns.extend([
        fake_openai.tool_turn('analisar_transacoes', '{}'),
        fake_openai.text_turn('Você gastou 42,00.'),
    ])

    events = eventos(allowed.post(reverse('assistant:stream'), {'message': 'Quanto gastei?'}))

    assert [event['type'] for event in events] == ['tool', 'delta', 'done']

    saida = fake_openai.calls[1]['input'][-1]
    assert saida['type'] == 'function_call_output'
    assert json.loads(saida['output'])['total']['outcome'] == '42.00'

    conversa = Conversation.objects.get(user=user)
    assert list(conversa.messages.values_list('role', flat=True)) == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert allowed.get(reverse('assistant:history')).json()['blocks'] == [
        {'kind': 'message', 'role': 'user', 'content': 'Quanto gastei?', 'attachment': None},
        {'kind': 'message', 'role': 'assistant', 'content': 'Você gastou 42,00.', 'attachment': None},
    ]


def test_erro_de_parametro_volta_ao_modelo_sem_derrubar_a_conversa(user, fake_openai):
    conversa = Conversation.objects.create(user=user)
    fake_openai.turns.extend([
        fake_openai.tool_turn('analisar_transacoes', '{"start": "ontem"}'),
        fake_openai.text_turn('Qual período?'),
    ])

    list(converse(conversa, user, 'Quanto gastei?'))

    saida = json.loads(conversa.messages.filter(role=Role.TOOL).get().content)
    assert saida['ok'] is False
    assert 'AAAA-MM-DD' in saida['error']


def test_json_quebrado_volta_como_erro(user, fake_openai):
    conversa = Conversation.objects.create(user=user)
    fake_openai.turns.extend([
        fake_openai.tool_turn('analisar_transacoes', '{quebrado'),
        fake_openai.text_turn('Ok.'),
    ])

    list(converse(conversa, user, 'Oi'))

    assert json.loads(conversa.messages.filter(role=Role.TOOL).get().content)['ok'] is False


def test_chamada_identica_a_uma_que_falhou_nao_roda_de_novo(user, fake_openai, monkeypatch):
    conversa = Conversation.objects.create(user=user)
    executadas = []
    original = client_module.execute
    monkeypatch.setattr('assistant.client.execute', lambda call, *args: executadas.append(call) or original(call, *args))
    fake_openai.turns.extend([
        fake_openai.tool_turn('analisar_transacoes', '{"start": "ontem"}', call_id='call_1'),
        fake_openai.tool_turn('analisar_transacoes', '{"start": "ontem"}', call_id='call_2'),
        fake_openai.text_turn('Qual período?'),
    ])

    list(converse(conversa, user, 'Quanto gastei?'))

    assert len(executadas) == 1
    saidas = [json.loads(message.content) for message in conversa.messages.filter(role=Role.TOOL)]
    assert 'idêntica' in saidas[1]['error']


def test_modelo_em_laco_para_no_teto(user, fake_openai):
    conversa = Conversation.objects.create(user=user)
    fake_openai.turns.extend(fake_openai.tool_turn('consultar_saldo', '{}', call_id=f'call_{n}') for n in range(MAX_ROUNDS))

    events = list(converse(conversa, user, 'Saldo?'))

    assert events[-1]['type'] == 'error'
    assert len(fake_openai.calls) == MAX_ROUNDS


def test_historico_nao_comeca_por_resposta_de_ferramenta_orfa(user):
    conversa = Conversation.objects.create(user=user)
    Message.objects.create(conversation=conversa, role=Role.TOOL, items=[{'type': 'function_call_output', 'call_id': 'x', 'output': '{}'}])
    for n in range(HISTORY_LIMIT - 1):
        Message.objects.create(conversation=conversa, role=Role.USER, items=[{'role': 'user', 'content': str(n)}])

    items = history(conversa)

    assert items[0] == {'role': 'user', 'content': '0'}
    assert len(items) == HISTORY_LIMIT - 1
