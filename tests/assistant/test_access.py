import pytest
from django.urls import reverse

from assistant.models import Conversation


ROUTES = [('get', 'assistant:history'), ('post', 'assistant:stream'), ('post', 'assistant:reset')]


@pytest.mark.parametrize('method, route', ROUTES)
def test_rota_sem_permissao_responde_403(logged, method, route):
    assert getattr(logged, method)(reverse(route)).status_code == 403


@pytest.mark.parametrize('method, route', ROUTES)
def test_rota_sem_sessao_nao_responde(client, db, method, route):
    assert getattr(client, method)(reverse(route)).status_code in (302, 403)


def test_pagina_sem_sessao_vai_para_o_login(client, db):
    response = client.get(reverse('assistant:page'))
    assert response.status_code == 302
    assert reverse('app:login') in response['Location']


def test_pagina_sem_permissao_responde_403(logged):
    assert logged.get(reverse('assistant:page')).status_code == 403


def test_atalho_e_menu_so_aparecem_com_permissao(logged, user, use_assistant):
    page = logged.get(reverse('app:overview')).content.decode()
    assert 'id="assistant"' not in page
    assert reverse('assistant:page') not in page

    user.user_permissions.add(use_assistant)
    page = logged.get(reverse('app:overview')).content.decode()
    assert 'assistant-floating' in page
    assert reverse('assistant:page') in page


def test_pagina_traz_o_chat_embutido_sem_o_atalho(allowed):
    page = allowed.get(reverse('assistant:page')).content.decode()
    assert 'assistant-embedded' in page
    assert 'assistant-toggle' not in page


def test_mensagem_vazia_e_recusada(allowed):
    assert allowed.post(reverse('assistant:stream'), {'message': '  '}).status_code == 400


def test_historico_vazio(allowed):
    assert allowed.get(reverse('assistant:history')).json() == {'blocks': []}


def test_limpar_apaga_so_a_propria_conversa(allowed, user, other_user):
    Conversation.objects.create(user=user)
    Conversation.objects.create(user=other_user)

    allowed.post(reverse('assistant:reset'))

    assert list(Conversation.objects.values_list('user', flat=True)) == [other_user.pk]
