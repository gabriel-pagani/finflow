"""
O teto de palpites de senha por usuário.

O axes tranca o par usuário e IP, e quem troca de endereço recomeça o contador
dele: cinco palpites por IP, sem fim, no mesmo usuário. O teto testado aqui
conta só o usuário, e é o que enxerga o ataque que vem de muitas redes.
"""
from uuid import uuid4

import pytest
from django.contrib.auth import authenticate
from django.urls import reverse

from app.forms import LOGIN_THROTTLED_ERROR
from app.utils import throttle


@pytest.fixture(autouse=True)
def cache_isolado(settings):
    """
    O contador mora no cache, que no ambiente de teste é o Redis do compose. Um
    LocMem com nome próprio por teste começa zerado sem que nada precise ser
    limpo: o palpite de um teste não sobra para o seguinte.
    """
    settings.CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': uuid4().hex},
    }


@pytest.fixture(autouse=True)
def teto_baixo(monkeypatch):
    """
    Três palpites em vez de cinquenta. Cada erro custa um hash de Argon2, e o
    teto de verdade deixaria a suíte esperando por nada — o que se testa aqui é
    o contador, e não o número.
    """
    monkeypatch.setattr(throttle, 'LOGIN_FAILURES_LIMIT', 3)


# Um IP por palpite: o teto do axes, que é por par, nunca chega a valer, e o que
# recusar a tentativa só pode ser o teto por usuário.
REDES = ['198.51.100.1', '198.51.100.2', '198.51.100.3', '198.51.100.4', '198.51.100.5']


def entrar(client, username='gabriel', password='segredo', ip='127.0.0.1'):
    return client.post(reverse('app:login'), {'username': username, 'password': password}, REMOTE_ADDR=ip)


def errar(client, vezes, username='gabriel'):
    for ip in REDES[:vezes]:
        entrar(client, username=username, password='chute', ip=ip)


def test_o_teto_vale_mesmo_trocando_de_ip(client, user):
    errar(client, throttle.LOGIN_FAILURES_LIMIT)

    response = entrar(client, password='chute', ip=REDES[3])

    assert LOGIN_THROTTLED_ERROR in response.content.decode()


def test_passado_o_teto_nem_a_senha_certa_entra(client, user):
    errar(client, throttle.LOGIN_FAILURES_LIMIT)

    response = entrar(client, ip=REDES[3])

    assert response.status_code == 200
    assert '_auth_user_id' not in client.session


def test_o_acerto_zera_a_conta(client, user):
    errar(client, throttle.LOGIN_FAILURES_LIMIT - 1)

    assert entrar(client).status_code == 302
    client.logout()
    errar(client, throttle.LOGIN_FAILURES_LIMIT - 1)

    assert entrar(client).status_code == 302


def test_o_teto_e_do_usuario_errado_e_nao_da_rede(client, user, other_user):
    errar(client, throttle.LOGIN_FAILURES_LIMIT)

    response = entrar(client, username='mariana', ip=REDES[0])

    assert response.status_code == 302
    assert '_auth_user_id' in client.session


def test_o_teto_nao_olha_a_caixa_do_usuario(client, user):
    errar(client, throttle.LOGIN_FAILURES_LIMIT, username='GaBrIeL')

    response = entrar(client)

    assert LOGIN_THROTTLED_ERROR in response.content.decode()


def test_o_teto_vale_para_toda_porta_que_autentica(client, user, rf):
    """
    Ele está no backend, e não na tela: o login do portal de administração usa
    outro formulário, e nem passa pelo teto do nginx, que é só do /login/.
    """
    errar(client, throttle.LOGIN_FAILURES_LIMIT)

    assert authenticate(rf.post('/'), username='gabriel', password='segredo') is None
