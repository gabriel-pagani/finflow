import hashlib
import hmac
import time

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.models import BankConnection


SECRET = 'whsec_' + 'a' * 64
BODY = b'{"event":"transactions/created","eventId":"1","itemId":"x"}'


def sign(body=BODY, secret=SECRET, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


@pytest.fixture(autouse=True)
def secret(settings):
    settings.PLUGGY_WEBHOOK_SECRET = SECRET


@pytest.fixture
def client():
    # Igual à Pluggy: sem sessão e com o CSRF conferido de verdade.
    return Client(enforce_csrf_checks=True)


def post(client, body=BODY, **headers):
    return client.post(reverse('app:pluggy_webhook'), body, content_type='application/json', headers=headers)


@pytest.mark.django_db
def test_assinatura_valida_e_aceita_sem_login_nem_csrf(client):
    assert post(client, **{'X-Pluggy-Signature': sign()}).status_code == 200


# As assinaturas são montadas na hora do teste: geradas na coleta, a de 301 s
# no futuro já cairia dentro da folga quando a suíte chegasse até aqui.
@pytest.mark.django_db
@pytest.mark.parametrize('header', [
    lambda: None,
    lambda: 'lixo',
    lambda: sign(secret='whsec_outro'),
    lambda: sign(body=b'{"event":"outro"}'),
    lambda: sign(timestamp=int(time.time()) - 301),
    lambda: sign(timestamp=int(time.time()) + 301),
], ids=['sem-cabecalho', 'lixo', 'outro-segredo', 'corpo-alterado', 'velha', 'futura'])
def test_assinatura_ausente_errada_ou_velha_e_recusada(client, header):
    header = header()
    headers = {'X-Pluggy-Signature': header} if header else {}
    assert post(client, **headers).status_code == 401


@pytest.mark.django_db
def test_sem_segredo_configurado_recusa_tudo(client, settings):
    settings.PLUGGY_WEBHOOK_SECRET = ''
    assert post(client, **{'X-Pluggy-Signature': sign(secret='')}).status_code == 401


@pytest.mark.django_db
def test_get_nao_e_permitido(client):
    assert client.get(reverse('app:pluggy_webhook')).status_code == 405


@pytest.mark.django_db
def test_aviso_marca_a_conexao_para_buscar_de_novo(client, user):
    connection = BankConnection.objects.create(user=user, item_id='item-1', synced_at=timezone.now())
    other = BankConnection.objects.create(user=user, item_id='item-2', synced_at=timezone.now())
    body = b'{"event":"transactions/created","itemId":"item-1"}'

    assert post(client, body, **{'X-Pluggy-Signature': sign(body)}).status_code == 200

    connection.refresh_from_db()
    other.refresh_from_db()
    assert connection.synced_at is None and other.synced_at is not None
