"""
O pedido de conta feito da tela de login.

Três coisas precisam valer ao mesmo tempo: o cadastro nasce desligado, ele não
entra enquanto ninguém liberar, e o mesmo IP não pede de novo antes da janela.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from app.forms import ACCESS_REQUEST_ATTEMPTS_LIMIT, ACCESS_REQUEST_TOO_MANY_ATTEMPTS
from app.models import AccessRequest


@pytest.fixture(autouse=True)
def cache_isolado(settings):
    """
    A contagem de tentativas mora no cache, que no ambiente de teste é o Redis do
    compose. Um LocMem com nome próprio por teste começa zerado sem que nada
    precise ser limpo: a tentativa de um teste não sobra para o seguinte, nem
    para a próxima rodada, nem vai parar no Redis.
    """
    settings.CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': uuid4().hex},
    }


SENHA = 'trilha-funda-2026'

PEDIDO = {
    'first_name': 'Mariana',
    'last_name': 'Souza',
    'username': 'mariana',
    'email': 'mariana@exemplo.com',
    'password1': SENHA,
    'password2': SENHA,
}


def pedir(client, **fields):
    return client.post(reverse('app:access_request'), {**PEDIDO, **fields})


def entrar(client, username, password):
    return client.post(reverse('app:login'), {'username': username, 'password': password})


def test_pedido_cria_o_cadastro_desligado(client, db):
    response = pedir(client)

    assert response.status_code == 302
    pedinte = get_user_model().objects.get(username='mariana')
    assert pedinte.is_active is False
    assert pedinte.check_password(SENHA)
    assert pedinte.email == 'mariana@exemplo.com'


def test_pedido_registra_de_onde_veio(client, db):
    pedir(client)

    pedinte = get_user_model().objects.get(username='mariana')
    pedido = AccessRequest.objects.get()

    assert pedido.ip == '127.0.0.1'
    assert pedido.user == pedinte
    # Na lista do portal, o cadastro pedido e o criado à mão são a mesma linha
    # desligada; a observação é o que os separa.
    assert 'solicitado' in pedinte.observations.lower()


def test_cadastro_desligado_nao_entra(client, db):
    pedir(client)

    response = entrar(client, 'mariana', SENHA)

    assert response.status_code == 200
    assert '_auth_user_id' not in client.session


def test_cadastro_liberado_entra(client, db):
    pedir(client)
    get_user_model().objects.filter(username='mariana').update(is_active=True)

    response = entrar(client, 'mariana', SENHA)

    assert response.status_code == 302
    assert '_auth_user_id' in client.session


def test_mesmo_ip_nao_pede_duas_vezes_na_semana(client, db):
    pedir(client)

    response = pedir(client, username='outra', email='outra@exemplo.com')

    assert response.status_code == 200
    assert not get_user_model().objects.filter(username='outra').exists()
    assert 'sete dias' in response.content.decode()


def test_passada_a_semana_o_ip_pede_de_novo(client, db):
    pedir(client)
    AccessRequest.objects.update(created_at=timezone.now() - AccessRequest.WINDOW - timedelta(minutes=1))

    response = pedir(client, username='outra', email='outra@exemplo.com')

    assert response.status_code == 302
    assert get_user_model().objects.filter(username='outra').exists()


def test_o_teto_e_por_ip(client, db):
    pedir(client)

    response = client.post(
        reverse('app:access_request'),
        {**PEDIDO, 'username': 'outra', 'email': 'outra@exemplo.com'},
        REMOTE_ADDR='203.0.113.7',
    )

    assert response.status_code == 302
    assert AccessRequest.objects.count() == 2


def test_pedido_recusado_nao_gasta_a_semana(client, db):
    """A senha curta nem chega a criar cadastro, então não pode segurar o IP."""
    pedir(client, password1='123', password2='123')

    assert not AccessRequest.objects.exists()
    assert pedir(client).status_code == 302


def test_usuario_existente_nao_e_sobrescrito(client, user):
    response = pedir(client, username='gabriel')

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active is True
    assert user.check_password('segredo')


def test_email_ja_cadastrado_e_recusado(client, user):
    get_user_model().objects.filter(pk=user.pk).update(email='mariana@exemplo.com')

    response = pedir(client)

    assert response.status_code == 200
    assert not get_user_model().objects.filter(username='mariana').exists()


@pytest.mark.parametrize('campo, recado', [
    ({'username': 'gabriel'}, 'Um usuário com este nome de usuário já existe.'),
    ({'email': 'gabriel@exemplo.com'}, 'Já existe um usuário com este e-mail.'),
])
def test_passado_o_teto_a_tela_nao_conta_quem_tem_conta(client, user, campo, recado):
    get_user_model().objects.filter(pk=user.pk).update(email='gabriel@exemplo.com')

    # Recusadas, mas contadas: é na recusa que a tela conta que a conta existe.
    for _ in range(ACCESS_REQUEST_ATTEMPTS_LIMIT):
        assert recado in pedir(client, **campo).content.decode()

    content = pedir(client, **campo).content.decode()

    assert ACCESS_REQUEST_TOO_MANY_ATTEMPTS in content
    assert recado not in content


def test_passado_o_teto_nem_o_pedido_valido_entra(client, db):
    for _ in range(ACCESS_REQUEST_ATTEMPTS_LIMIT):
        pedir(client, password1='123', password2='123')

    response = pedir(client)

    assert response.status_code == 200
    assert not get_user_model().objects.filter(username='mariana').exists()
    assert not AccessRequest.objects.exists()


def test_o_teto_de_tentativas_e_por_ip(client, db):
    for _ in range(ACCESS_REQUEST_ATTEMPTS_LIMIT + 1):
        pedir(client, password1='123', password2='123')

    response = client.post(reverse('app:access_request'), PEDIDO, REMOTE_ADDR='203.0.113.7')

    assert response.status_code == 302
    assert get_user_model().objects.filter(username='mariana').exists()


def test_quem_ja_entrou_nao_ve_a_tela(logged):
    assert logged.get(reverse('app:access_request')).status_code == 302


def test_a_entrada_leva_ao_pedido(client, db):
    assert reverse('app:access_request') in client.get(reverse('app:login')).content.decode()


def liberar(admin, selecionados):
    """Chama a ação do portal direto, sem passar pela tela protegida por OTP."""
    from django.contrib.admin.sites import site
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from app.admin.user import UserAdmin

    request = RequestFactory().post('/')
    request.user = admin
    request.session = {}
    request._messages = FallbackStorage(request)

    UserAdmin(get_user_model(), site).release_access(request, selecionados)


def test_liberar_liga_so_quem_tem_pedido(client, user, db):
    pedir(client)
    pedinte = get_user_model().objects.get(username='mariana')
    # Desativado por outro motivo: sem pedido, não é assunto da ação.
    cortado = get_user_model().objects.create_user(username='cortado', password=SENHA, is_active=False)

    liberar(user, get_user_model().objects.filter(pk__in=[pedinte.pk, cortado.pk]))

    pedinte.refresh_from_db()
    cortado.refresh_from_db()
    assert pedinte.is_active is True
    assert cortado.is_active is False


def test_liberar_apaga_o_pedido_atendido(client, user, db):
    pedir(client)
    pedinte = get_user_model().objects.get(username='mariana')
    esperando = AccessRequest.objects.create(user=user, ip='203.0.113.7')

    liberar(user, get_user_model().objects.filter(pk=pedinte.pk))

    assert not AccessRequest.objects.filter(user=pedinte).exists()
    # O pedido de quem ainda espera continua segurando o IP dele.
    assert AccessRequest.objects.filter(pk=esperando.pk).exists()
