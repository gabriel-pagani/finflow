from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from assistant.client import ModelError
from assistant.models import MAX_MESSAGE, Attachment, Command, Conversation, DailyUsage, Message, Role


JPEG = b'\xff\xd8\xff' + b'\x00' * 64
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 64


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def command(user):
    return Command.objects.create(user=user, name='saldo', instructions='Mostre o saldo.')


def enviar(client, message='Quanto gastei?', **files):
    response = client.post(reverse('assistant:stream'), {'message': message, **files})
    if response.streaming:
        b''.join(response.streaming_content)
    return response


def usadas(user):
    usage = DailyUsage.objects.filter(user=user, day=timezone.localdate()).first()
    return usage.messages if usage else 0


def gastar(user, total, day=None):
    DailyUsage.objects.create(user=user, day=day or timezone.localdate(), messages=total)


def faixa(user, codename):
    user.user_permissions.add(Permission.objects.get(codename=codename, content_type__app_label='assistant'))


def test_sem_faixa_para_em_vinte_por_dia(allowed, user, fake_openai):
    gastar(user, DailyUsage.LIMIT - 1)
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    assert enviar(allowed).status_code == 200
    response = enviar(allowed)

    assert response.status_code == 429
    assert f'as {DailyUsage.LIMIT} mensagens de hoje' in response.json()['error']
    assert usadas(user) == DailyUsage.LIMIT
    assert len(fake_openai.calls) == 1
    assert Message.objects.filter(role=Role.USER).count() == 1


def test_texto_foto_audio_e_comando_contam_igual(allowed, user, command, fake_openai, monkeypatch):
    monkeypatch.setattr('assistant.client.transcribe', lambda upload: 'gastei 30 no mercado')
    fake_openai.turns.extend(fake_openai.text_turn('Ok.') for _ in range(4))

    enviar(allowed, 'Quanto gastei?')
    enviar(allowed, '', file=SimpleUploadedFile('foto.jpg', JPEG))
    enviar(allowed, '', file=SimpleUploadedFile('audio.webm', WEBM))
    enviar(allowed, '/saldo')

    assert usadas(user) == 4
    assert len(fake_openai.calls) == 4


@pytest.mark.parametrize('message, files', [
    ('Quanto gastei?', {}),
    ('', {'file': SimpleUploadedFile('foto.jpg', JPEG)}),
    ('', {'file': SimpleUploadedFile('audio.webm', WEBM)}),
    ('/saldo', {}),
], ids=['texto', 'foto', 'audio', 'comando'])
def test_no_limite_nenhum_tipo_passa(allowed, user, command, fake_openai, monkeypatch, message, files):
    monkeypatch.setattr('assistant.client.transcribe', lambda upload: pytest.fail('transcreveu além do limite'))
    gastar(user, DailyUsage.LIMIT)

    response = enviar(allowed, message, **files)

    assert response.status_code == 429
    assert usadas(user) == DailyUsage.LIMIT
    assert not Conversation.objects.exists()
    assert not Attachment.objects.exists()
    assert fake_openai.calls == []


@pytest.mark.parametrize('data', [
    {'message': ''},
    {'message': 'a' * (MAX_MESSAGE + 1)},
    {'message': '/inexistente'},
    {'message': '', 'file': SimpleUploadedFile('foto.jpg', b'nao sou uma imagem')},
], ids=['vazia', 'longa', 'comando inexistente', 'arquivo invalido'])
def test_mensagem_recusada_nao_gasta_a_cota(allowed, user, fake_openai, data):
    response = allowed.post(reverse('assistant:stream'), data)

    assert response.status_code == 400
    assert usadas(user) == 0


def test_envio_aceito_conta_mesmo_se_o_audio_nao_se_transcrever(allowed, user, monkeypatch):
    def falha(upload):
        raise ModelError('A transcrição voltou vazia.')

    monkeypatch.setattr('assistant.client.transcribe', falha)

    response = enviar(allowed, '', file=SimpleUploadedFile('audio.webm', WEBM))

    assert response.status_code == 200
    assert not Message.objects.exists()
    assert usadas(user) == 1


def test_limpar_a_conversa_nao_devolve_a_cota(allowed, user, fake_openai):
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))
    enviar(allowed)

    allowed.post(reverse('assistant:reset'))

    assert not Conversation.objects.exists()
    assert usadas(user) == 1


def test_cota_de_ontem_nao_conta_hoje(allowed, user, fake_openai):
    gastar(user, DailyUsage.LIMIT, day=timezone.localdate() - timedelta(days=1))
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    assert enviar(allowed).status_code == 200
    assert usadas(user) == 1


def test_cota_de_outro_usuario_nao_conta(allowed, user, other_user, fake_openai):
    gastar(other_user, DailyUsage.LIMIT)
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    assert enviar(allowed).status_code == 200
    assert usadas(user) == 1


@pytest.mark.parametrize('codename, limit', [('message_limit_50', 50), ('message_limit_100', 100)])
def test_faixa_da_permissao_define_o_teto(allowed, user, fake_openai, codename, limit):
    faixa(user, codename)
    gastar(user, limit - 1)
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    assert enviar(allowed).status_code == 200
    response = enviar(allowed)

    assert response.status_code == 429
    assert f'as {limit} mensagens de hoje' in response.json()['error']
    assert usadas(user) == limit


def test_com_mais_de_uma_faixa_vale_a_maior(user):
    faixa(user, 'message_limit_50')
    faixa(user, 'message_limit_100')

    assert DailyUsage.limit_for(user) == 100


@pytest.mark.parametrize('promote', [
    lambda user: faixa(user, 'unlimited_messages'),
    lambda user: setattr(user, 'is_superuser', True) or user.save(),
], ids=['permissao', 'superusuario'])
def test_ilimitado_nao_tem_teto_mas_continua_contando(allowed, user, fake_openai, promote):
    promote(user)
    gastar(user, 100)
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    assert enviar(allowed).status_code == 200
    assert usadas(user) == 101
