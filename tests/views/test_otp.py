"""
O segundo fator, que vale para todo usuário e não só para o portal.

Quem já cadastrou o aplicativo não entra sem o código; quem ainda não cadastrou
entra com a senha, mas não anda no sistema antes de cadastrar.
"""
from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from app.utils.otp import secret_of
from app.views.auth import PENDING, PENDING_TIMEOUT
from tests.conftest import sign_in


SENHA = 'segredo'


@pytest.fixture
def use_assistant(db):
    return Permission.objects.get(codename='use_assistant', content_type__app_label='assistant')


def codigo(device, drift=0):
    """O código que o aplicativo mostraria agora para este dispositivo."""
    return format(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits, drift=drift), f'0{device.digits}d')


def entrar(client, user, **extra):
    """A primeira etapa: usuário e senha."""
    return client.post(reverse('app:login'), {'username': user.username, 'password': SENHA, **extra})


def confirmar(client, token):
    """A segunda etapa: o código, na tela que só existe depois da senha."""
    return client.post(reverse('app:login_token'), {'token': token})


def verificada(client):
    return DEVICE_ID_SESSION_KEY in client.session


# Login -------------------------------------------------------------------

def test_a_tela_da_senha_nao_mostra_campo_de_codigo(client, user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)

    corpo = client.get(reverse('app:login')).content.decode()

    # Antes do envio o servidor não sabe quem está digitando, então o campo não
    # tem como aparecer aqui para uns e não para outros: ele mora na etapa dois.
    assert 'Código' not in corpo


def test_a_senha_sozinha_nao_abre_a_sessao(client, user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)

    response = entrar(client, user)

    assert response['Location'] == reverse('app:login_token')
    assert '_auth_user_id' not in client.session


def test_a_segunda_etapa_pede_o_codigo(client, user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user)

    corpo = client.get(reverse('app:login_token')).content.decode()

    assert 'name="token"' in corpo


def test_codigo_errado_nao_entra(client, user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user)

    response = confirmar(client, '000000')

    assert response.status_code == 200
    assert 'Código inválido' in response.content.decode()
    assert '_auth_user_id' not in client.session


def test_codigo_certo_entra_e_verifica_a_sessao(client, user):
    device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user)

    response = confirmar(client, codigo(device))

    assert response.status_code == 302
    assert client.session['_auth_user_id'] == str(user.pk)
    assert verificada(client)
    assert client.get(reverse('app:overview')).status_code == 200


def test_senha_errada_nao_chega_na_segunda_etapa(client, user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)

    response = client.post(reverse('app:login'), {'username': user.username, 'password': 'outra'})

    assert 'Usuário e/ou senha inválidos!' in response.content.decode()
    assert client.get(reverse('app:login_token'))['Location'] == reverse('app:login')


def test_codigo_de_outro_usuario_nao_serve(client, user, other_user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    alheio = TOTPDevice.objects.create(user=other_user, name='default', confirmed=True)
    entrar(client, user)

    response = confirmar(client, codigo(alheio))

    assert 'Código inválido' in response.content.decode()
    assert '_auth_user_id' not in client.session


# A espera entre as duas etapas ---------------------------------------------

def test_a_segunda_etapa_sem_a_senha_volta_para_o_login(client, user):
    assert client.get(reverse('app:login_token'))['Location'] == reverse('app:login')
    assert confirmar(client, '000000')['Location'] == reverse('app:login')


def test_a_espera_vence_e_a_senha_e_pedida_de_novo(client, user):
    device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user)

    # Como se a pessoa tivesse deixado a tela aberta além do tempo.
    session = client.session
    session[PENDING] = {**session[PENDING], 'since': (timezone.now() - PENDING_TIMEOUT - timedelta(seconds=1)).isoformat()}
    session.save()

    assert confirmar(client, codigo(device))['Location'] == reverse('app:login')
    assert '_auth_user_id' not in client.session


def test_voltar_para_o_login_descarta_a_espera(client, user):
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user)

    client.get(reverse('app:login'))

    assert PENDING not in client.session


def test_conta_desligada_entre_as_etapas_nao_entra(client, user):
    device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user)

    user.is_active = False
    user.save(update_fields=['is_active'])

    assert confirmar(client, codigo(device))['Location'] == reverse('app:login')
    assert '_auth_user_id' not in client.session


def test_o_destino_pedido_no_login_sobrevive_as_duas_etapas(client, user):
    device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    entrar(client, user, next=reverse('app:cards_list'))

    assert confirmar(client, codigo(device))['Location'] == reverse('app:cards_list')


# Primeiro login ----------------------------------------------------------

def test_quem_nao_cadastrou_entra_com_a_senha_e_vai_cadastrar(client, user):
    response = entrar(client, user)

    assert response.status_code == 302
    assert client.session['_auth_user_id'] == str(user.pk)
    assert not verificada(client)
    assert client.get(reverse('app:overview'))['Location'] == reverse('app:otp_setup')


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list', 'app:cards_list'])
def test_sistema_fica_bloqueado_ate_cadastrar(client, user, route):
    client.force_login(user)

    assert client.get(reverse(route))['Location'] == reverse('app:otp_setup')


def test_o_assistente_tambem_fica_bloqueado(client, user, use_assistant):
    user.user_permissions.add(use_assistant)
    client.force_login(user)

    assert client.get(reverse('assistant:page'))['Location'] == reverse('app:otp_setup')


def test_a_tela_de_cadastro_mostra_o_qr_code(client, user):
    client.force_login(user)

    corpo = client.get(reverse('app:otp_setup')).content.decode()

    # Desenhado aqui: o endereço com o segredo não passa por serviço de fora.
    assert '<svg' in corpo
    device = TOTPDevice.objects.get(user=user, confirmed=False)
    # A chave digitada à mão é a mesma que vai dentro do QR Code.
    assert f'secret={secret_of(device)}' in device.config_url
    assert secret_of(device) in corpo


def test_o_cadastro_nao_traz_menu_nem_assistente(client, user, use_assistant):
    # Daqui só se sai cadastrando ou saindo: o menu levaria a telas que devolvem
    # a pessoa para cá, e o chat só conversaria com esta mesma tela.
    user.user_permissions.add(use_assistant)
    client.force_login(user)

    corpo = client.get(reverse('app:otp_setup')).content.decode()

    assert 'assistant-panel' not in corpo
    assert 'Visão Geral' not in corpo


def test_recarregar_o_cadastro_mantem_o_mesmo_segredo(client, user):
    client.force_login(user)

    client.get(reverse('app:otp_setup'))
    client.get(reverse('app:otp_setup'))

    assert TOTPDevice.objects.filter(user=user).count() == 1


def test_cadastro_com_codigo_errado_nao_confirma(client, user):
    client.force_login(user)
    client.get(reverse('app:otp_setup'))

    response = client.post(reverse('app:otp_setup'), {'token': '000000'})

    assert response.status_code == 200
    assert not TOTPDevice.objects.filter(user=user, confirmed=True).exists()
    assert not verificada(client)


def test_cadastro_confirmado_libera_o_sistema(client, user):
    client.force_login(user)
    client.get(reverse('app:otp_setup'))
    device = TOTPDevice.objects.get(user=user, confirmed=False)

    response = client.post(reverse('app:otp_setup'), {'token': codigo(device)})

    assert response['Location'] == reverse('app:overview')
    device.refresh_from_db()
    assert device.confirmed
    assert verificada(client)
    assert client.get(reverse('app:overview')).status_code == 200


def test_quem_ja_cadastrou_nao_cadastra_de_novo(client, user):
    sign_in(client, user)

    response = client.get(reverse('app:otp_setup'))

    assert response['Location'] == reverse('app:overview')
    assert TOTPDevice.objects.filter(user=user).count() == 1


# Sessões e exceções ------------------------------------------------------

def test_sessao_aberta_antes_do_segundo_fator_e_encerrada(client, user):
    # Tem dispositivo, mas a sessão não passou por código nenhum: é o caso de
    # quem já estava dentro quando o segundo fator passou a valer.
    TOTPDevice.objects.create(user=user, name='default', confirmed=True)
    client.force_login(user)

    response = client.get(reverse('app:overview'))

    assert response['Location'] == reverse('app:login')
    assert '_auth_user_id' not in client.session


def test_o_pedido_de_conta_continua_aberto(client, user):
    client.force_login(user)

    # Não é redirecionado para o cadastro: é uma das telas de fora do sistema.
    assert client.get(reverse('app:access_request'))['Location'] == reverse('app:overview')


def test_sair_funciona_no_meio_do_cadastro(client, user):
    client.force_login(user)

    client.post(reverse('app:logout'))

    assert '_auth_user_id' not in client.session


def test_o_portal_nao_e_capturado_pelo_middleware(client, user, settings):
    # O portal tem o segundo fator dele, no próprio login; capturá-lo aqui
    # mandaria o administrador para a tela de cadastro do app.
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    client.force_login(user)

    # Montado como o urls.py monta, e não como se acha que ele monta: a variável
    # pode vir com ou sem barra no fim, e a rota sai diferente em cada caso.
    response = client.get(f'/{settings.ADMIN_PANEL_PATH}/')

    assert response.status_code in (200, 302)
    assert reverse('app:otp_setup') not in response.get('Location', '')
