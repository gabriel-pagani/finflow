"""
Os tetos que seguram o custo e o servidor.

O limite de mensagens por dia conta envios, não o que cada envio consome: a
conversa inteira sobe à API a cada rodada, uma rodada pode disparar várias
consultas, e o stream segura uma thread do começo ao fim. O que estes testes
cobrem é o que impede uma mensagem só de custar por um dia inteiro ou de tomar
o servidor de todo mundo.
"""
import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from assistant import attachments
from assistant.client import EXHAUSTED, HISTORY_BUDGET, MAX_ROUNDS, MAX_TOOL_OUTPUT, converse, history
from assistant.models import MAX_MESSAGE, Conversation, DailyUsage, Message, Role
from assistant.views import STREAM_LOCK


JPEG = b'\xff\xd8\xff' + b'\x00' * 64
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 64


class Contado(SimpleUploadedFile):
    """Um upload que anota quantos bytes saíram do arquivo."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lidos = 0

    def read(self, *args, **kwargs):
        data = super().read(*args, **kwargs)
        self.lidos += len(data)
        return data


@pytest.fixture
def conversation(user):
    return Conversation.objects.create(user=user)


@pytest.fixture(autouse=True)
def unlocked(user):
    """Nenhum cadeado preso de outro teste, e nenhum deixado para trás."""
    cache.delete(STREAM_LOCK.format(user.pk))
    yield
    cache.delete(STREAM_LOCK.format(user.pk))


def fala(conversation, text):
    return Message.objects.create(conversation=conversation, role=Role.USER, content=text, items=[{'role': 'user', 'content': text}])


def rodada(conversation, output, call_id='call_1'):
    """A chamada de ferramenta e o retorno dela, como uma rodada os grava."""
    Message.objects.create(conversation=conversation, role=Role.ASSISTANT, content='',
                           items=[{'type': 'function_call', 'name': 'listar_transacoes', 'arguments': '{}', 'call_id': call_id}])
    Message.objects.create(conversation=conversation, role=Role.TOOL, content=output,
                           items=[{'type': 'function_call_output', 'call_id': call_id, 'output': output}])


def outputs(conversation):
    return [json.loads(message.content) for message in conversation.messages.filter(role=Role.TOOL)]


def used(user):
    usage = DailyUsage.objects.filter(user=user, day=timezone.localdate()).first()
    return usage.messages if usage else 0


# Anexo ------------------------------------------------------------------

def test_arquivo_recusado_nao_e_lido_inteiro():
    upload = Contado('planilha.csv', b'a,b,c\n' + b'0' * 5 * 1024 * 1024)

    with pytest.raises(attachments.UploadError):
        attachments.inspect(upload)

    assert upload.lidos <= 16


def test_foto_acima_do_teto_e_recusada_pelo_tamanho_declarado():
    upload = Contado('foto.jpg', JPEG + b'\x00' * attachments.LIMITS['image'])

    with pytest.raises(attachments.UploadError):
        attachments.inspect(upload)

    assert upload.lidos <= 16


def test_audio_acima_do_teto_e_recusado():
    with pytest.raises(attachments.UploadError):
        attachments.inspect(SimpleUploadedFile('audio.webm', WEBM + b'\x00' * attachments.LIMITS['audio']))


# Áudio ------------------------------------------------------------------

def test_transcricao_longa_demais_nao_vira_mensagem(user, conversation, fake_openai, monkeypatch):
    monkeypatch.setattr('assistant.client.transcribe', lambda upload: 'a' * (MAX_MESSAGE + 1))
    upload = attachments.Upload(kind='audio', mime='audio/webm', extension='.webm', data=WEBM)

    events = list(converse(conversation, user, '', upload))

    assert events[-1]['type'] == 'error'
    assert 'longo demais' in events[-1]['message']
    assert not conversation.messages.exists()
    assert fake_openai.calls == []


# Consultas --------------------------------------------------------------

def test_retorno_grande_demais_nao_entra_na_conversa(user, conversation, fake_openai, monkeypatch):
    monkeypatch.setattr('assistant.client.execute', lambda *args: ({'ok': True, 'rows': 'x' * MAX_TOOL_OUTPUT}, None))
    fake_openai.turns.extend([
        fake_openai.tool_turn('listar_transacoes', '{}'),
        fake_openai.text_turn('Estreite o período.'),
    ])

    list(converse(conversation, user, 'Liste tudo'))

    saida = outputs(conversation)[0]
    assert saida['ok'] is False
    assert 'grande demais' in saida['error']


def test_orcamento_da_mensagem_barra_as_consultas_seguintes(user, conversation, fake_openai, monkeypatch):
    executadas = []

    def gorda(call, *args):
        executadas.append(call)
        return {'ok': True, 'rows': 'x' * (MAX_TOOL_OUTPUT - 100)}, None

    monkeypatch.setattr('assistant.client.execute', gorda)
    # Uma rodada por consulta, todas diferentes entre si: o que as segura é o
    # orçamento da mensagem, e não a recusa de repetir uma chamada que falhou.
    fake_openai.turns.extend(
        fake_openai.tool_turn('listar_transacoes', json.dumps({'offset': number}), call_id=f'call_{number}')
        for number in range(MAX_ROUNDS)
    )

    list(converse(conversation, user, 'Liste tudo, de página em página'))

    assert len(executadas) < MAX_ROUNDS
    assert outputs(conversation)[-1] == EXHAUSTED


# Histórico --------------------------------------------------------------

def test_rodada_antiga_grande_fica_de_fora(conversation):
    fala(conversation, 'liste tudo')
    rodada(conversation, 'x' * (HISTORY_BUDGET + 1))
    fala(conversation, 'e agora?')

    assert history(conversation) == [{'role': 'user', 'content': 'e agora?'}]


def test_a_rodada_em_curso_vai_inteira(conversation):
    fala(conversation, 'liste tudo')
    rodada(conversation, 'x' * (HISTORY_BUDGET + 1))

    # Cortar aqui deixaria a chamada sem a resposta dela, e a API recusa a
    # conversa inteira nesse caso.
    assert [item.get('role') or item['type'] for item in history(conversation)] == ['user', 'function_call', 'function_call_output']


# Stream -----------------------------------------------------------------

def test_uma_resposta_por_vez_para_cada_usuario(allowed, user, fake_openai):
    fake_openai.turns.extend(fake_openai.text_turn('Ok.') for _ in range(2))
    aberto = allowed.post(reverse('assistant:stream'), {'message': 'Quanto gastei?'})

    recusado = allowed.post(reverse('assistant:stream'), {'message': 'E em agosto?'})

    assert recusado.status_code == 429
    assert 'mensagem anterior' in recusado.json()['error']
    # O recusado não gasta mensagem do dia: quem mandou duas de uma vez não perde
    # uma por causa disso.
    assert used(user) == 1

    b''.join(aberto.streaming_content)

    depois = allowed.post(reverse('assistant:stream'), {'message': 'E em agosto?'})
    b''.join(depois.streaming_content)

    assert depois.status_code == 200
    assert used(user) == 2


def test_a_vez_volta_mesmo_quando_o_stream_falha(allowed, user, fake_openai, monkeypatch):
    def morre(*args, **kwargs):
        raise RuntimeError('morreu no meio')

    monkeypatch.setattr('assistant.views.converse', morre)
    b''.join(allowed.post(reverse('assistant:stream'), {'message': 'Quanto gastei?'}).streaming_content)

    fake_openai.turns.append(fake_openai.text_turn('Ok.'))
    response = allowed.post(reverse('assistant:stream'), {'message': 'Quanto gastei?'})
    b''.join(response.streaming_content)

    assert response.status_code == 200
