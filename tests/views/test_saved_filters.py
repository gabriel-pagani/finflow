import pytest
from django.urls import reverse
from django.utils import timezone


def save(logged, route, query, action='save'):
    return logged.post(reverse(route), {'query': query, 'action': action})


def filters(response):
    return response.context['filters']


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list'])
def test_tela_sem_filtro_abre_o_filtro_salvo_sem_mudar_o_endereco(logged, route, account):
    save(logged, route, f'account={account.pk}')

    response = logged.get(reverse(route))
    assert response.status_code == 200
    assert filters(response)['account'] == [str(account.pk)]


def test_pagina_dois_continua_no_filtro_salvo(logged, account, debit_rule, make_transaction):
    for _ in range(26):
        make_transaction().save()
    save(logged, 'app:transactions_list', f'account={account.pk}')

    assert filters(logged.get(reverse('app:transactions_list'), {'page': 2}))['account'] == [str(account.pk)]


def test_cada_tela_tem_o_seu_filtro(logged, account):
    save(logged, 'app:overview', f'account={account.pk}')

    assert filters(logged.get(reverse('app:transactions_list')))['account'] == []


def test_url_com_filtro_passa_por_cima_do_salvo(logged, account):
    save(logged, 'app:overview', f'account={account.pk}')

    assert filters(logged.get(reverse('app:overview'), {'start': '2026-01-01'}))['account'] == []


def test_limpar_volta_para_o_filtro_salvo(logged, account):
    save(logged, 'app:overview', f'account={account.pk}')
    url = reverse('app:overview')

    assert f'href="{url}" title="Limpar"'.encode() in logged.get(url, {'start': '2026-01-01'}).content
    assert filters(logged.get(url))['account'] == [str(account.pk)]


def test_salvar_volta_para_o_endereco_limpo_e_guarda_so_o_filtro(logged, user, account):
    response = save(logged, 'app:transactions_list', f'account={account.pk}&page=2&only=filters')

    assert response.url == reverse('app:transactions_list')
    user.refresh_from_db()
    assert user.saved_filters == {'app:transactions_list': f'account={account.pk}'}


def test_salvar_de_novo_parte_do_filtro_aplicado(logged, account):
    save(logged, 'app:overview', f'account={account.pk}')

    assert f'account={account.pk}'.encode() in logged.get(reverse('app:overview')).content


def test_data_do_periodo_padrao_nao_congela(logged, user):
    year = timezone.localdate().year
    save(logged, 'app:overview', f'start={year}-01-01&end={year}-06-30')

    user.refresh_from_db()
    assert user.saved_filters['app:overview'] == f'end={year}-06-30'


def test_remover_padrao(logged, user, account):
    save(logged, 'app:overview', f'account={account.pk}')
    save(logged, 'app:overview', '', action='remove')

    user.refresh_from_db()
    assert user.saved_filters == {}
    assert filters(logged.get(reverse('app:overview')))['account'] == []


def saved_button(response):
    return response.context['is_saved_filter']


def test_botao_preenchido_so_com_o_filtro_salvo_em_uso(logged, account):
    url = reverse('app:overview')
    assert not saved_button(logged.get(url))

    save(logged, 'app:overview', f'account={account.pk}')
    assert saved_button(logged.get(url))
    assert b'value="remove"' in logged.get(url).content


def test_filtro_diferente_do_salvo_deixa_o_botao_para_salvar(logged, account, other_account):
    year = timezone.localdate().year
    save(logged, 'app:overview', f'account={account.pk}')
    url = reverse('app:overview')

    assert not saved_button(logged.get(url, {'account': other_account.pk}))
    assert not saved_button(logged.get(url, {'start': '2020-01-01'}))
    # O formulário manda as datas do período padrão e a busca vazia: continua sendo o salvo.
    assert saved_button(logged.get(url, {'start': f'{year}-01-01', 'end': f'{year}-12-31', 'account': account.pk}))


def test_campo_vazio_nao_entra_no_filtro_salvo(logged, user, account):
    save(logged, 'app:transactions_list', f'account={account.pk}&search=')

    user.refresh_from_db()
    assert user.saved_filters == {'app:transactions_list': f'account={account.pk}'}

