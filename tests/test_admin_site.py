"""
O axes e o TOTP aparecem no admin dentro da seção Sistema, com nome em português.
"""
import pytest
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory


MOVED = ['Tentativas de Acesso', 'Falhas de Acesso', 'Registros de Acesso', 'Dispositivos 2FA']


@pytest.fixture
def request_de_admin(db):
    request = RequestFactory().get('/')
    request.user = get_user_model().objects.create_superuser(username='admin', password='segredo')
    return request


def test_axes_e_totp_ficam_na_secao_sistema(request_de_admin):
    app_list = site.get_app_list(request_de_admin)
    labels = [app['app_label'] for app in app_list]
    sistema = next(app for app in app_list if app['app_label'] == 'app')

    assert 'axes' not in labels and 'otp_totp' not in labels
    assert set(MOVED) <= {model['name'] for model in sistema['models']}


@pytest.mark.parametrize('label', ['app', 'axes', 'otp_totp'])
def test_pagina_da_secao_mostra_o_sistema_inteiro(request_de_admin, label):
    [app] = site.get_app_list(request_de_admin, label)

    assert app['name'] == 'Sistema'
    assert set(MOVED) <= {model['name'] for model in app['models']}


def test_outras_secoes_nao_mudam(request_de_admin):
    [app] = site.get_app_list(request_de_admin, 'assistant')

    assert app['name'] == 'Assistente'
