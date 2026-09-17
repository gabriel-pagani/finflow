import pytest
from django.urls import reverse

from app.models import Category


def seen_by(route, card):
    """O que cada página enxerga.

    As opções seguem os métodos da página, então o lançamento que alimenta o
    filtro precisa ser do método certo: a previsão só olha crédito, e crédito
    pede cartão.
    """
    return {'method': 'CREDIT', 'card': card} if route == 'app:forecast' else {}


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list', 'app:cards_list'])
def test_paginas_renderizam(logged, route, card, category, transfer_in_rule):
    response = logged.get(reverse(route))
    assert response.status_code == 200, response.content[:500]


def test_login_renderiza(client):
    assert client.get(reverse('app:login')).status_code == 200


def test_filtros_e_busca(logged, account, category, debit_rule, make_transaction):
    make_transaction(category=category, description='Almoço').save()

    url = reverse('app:transactions_list')
    assert logged.get(url, {'search': 'almoco', 'account': account.pk, 'type': 'OUT', 'method': 'DEBIT', 'nature': 'REGULAR',
                            'start': '2026-01-01', 'end': '2026-12-31'}).status_code == 200


def test_filtro_com_valor_invalido_e_descartado(logged, debit_rule):
    assert logged.get(reverse('app:transactions_list'), {'account': 'lixo', 'category': 'x', 'type': 'ZZ', 'nature': 'ZZ'}).status_code == 200


def test_filtro_por_natureza(logged, account, debit_rule, make_transaction):
    normal = make_transaction()
    normal.save()
    interna = make_transaction(nature='INTERNAL')
    interna.save()

    response = logged.get(reverse('app:transactions_list'), {'nature': 'INTERNAL', 'start': '2026-01-01', 'end': '2026-12-31'})
    assert list(response.context['object_list']) == [interna]
    assert '<td>Interna</td>' in response.content.decode()


def test_interna_entra_no_saldo_e_fica_fora_dos_graficos_e_kpis(logged, account, category, debit_rule, make_transaction):
    make_transaction(category=category, value='10.00').save()
    make_transaction(nature='INTERNAL', value='500.00').save()

    response = logged.get(reverse('app:overview'), {'start': '2026-01-01', 'end': '2026-12-31'})
    saida = next(item for item in response.context['chart_months']['series'] if item['name'] == 'Saída')

    assert response.context['cards']['outcome'] == 10.0
    assert response.context['cards']['balance'] == -510.0
    assert sum(saida['data']) == 10.0
    assert response.context['chart_categories'] == [{'name': str(category), 'value': 10.0}]


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list'])
def test_filtro_oferece_so_contas_e_categorias_em_uso(logged, route, account, other_account, category, card,
                                                     other_user, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category, **seen_by(route, card)).save()
    make_transaction(user=other_user, account=other_account, category=lazer).save()

    response = logged.get(reverse(route))

    assert list(response.context['accounts']) == [account]
    assert list(response.context['categories']) == [category]


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list'])
def test_filtro_so_oferece_nao_identificada_com_transacao_sem_categoria(logged, route, category, card,
                                                                       other_user, other_account, make_transaction):
    visivel = seen_by(route, card)
    make_transaction(category=category, **visivel).save()
    # A de outro usuário não conta: a opção segue o que é meu.
    make_transaction(user=other_user, account=other_account).save()
    assert logged.get(reverse(route)).context['uncategorized_choices'] == []

    make_transaction(**visivel).save()
    assert logged.get(reverse(route)).context['uncategorized_choices'] == [('none', 'Categoria Não Identificada')]


@pytest.mark.parametrize('route', ['app:overview', 'app:forecast'])
def test_filtro_do_dashboard_segue_os_metodos_da_pagina(logged, route, account, other_account, category,
                                                       other_account_card, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category).save()
    make_transaction(account=other_account, card=other_account_card, method='CREDIT', category=lazer).save()

    response = logged.get(reverse(route))

    # A visão geral só olha débito e não se aplica; a previsão, só crédito.
    conta, categoria = {'app:overview': (account, category), 'app:forecast': (other_account, lazer)}[route]
    assert list(response.context['accounts']) == [conta]
    assert list(response.context['categories']) == [categoria]


def test_nao_identificada_do_dashboard_segue_os_metodos_da_pagina(logged, other_account, other_account_card,
                                                                 make_transaction):
    make_transaction(account=other_account, card=other_account_card, method='CREDIT').save()

    assert logged.get(reverse('app:overview')).context['uncategorized_choices'] == []
    assert logged.get(reverse('app:forecast')).context['uncategorized_choices'] == [('none', 'Categoria Não Identificada')]


def test_filtro_junta_categoria_e_nao_identificada(logged, category, debit_rule, make_transaction):
    lazer = Category.objects.create(description='Lazer')
    make_transaction(category=category).save()
    no_lazer = make_transaction(category=lazer)
    no_lazer.save()
    sem_categoria = make_transaction()
    sem_categoria.save()

    url = reverse('app:transactions_list')
    periodo = {'start': '2026-01-01', 'end': '2026-12-31'}

    response = logged.get(url, {**periodo, 'category': [lazer.pk, 'none']})
    assert set(response.context['object_list']) == {no_lazer, sem_categoria}

    response = logged.get(url, {**periodo, 'category': 'none'})
    assert list(response.context['object_list']) == [sem_categoria]


def test_grafico_de_categorias_respeita_a_nao_identificada(logged, category, debit_rule, make_transaction):
    make_transaction(category=category).save()
    make_transaction(value='30.00').save()

    response = logged.get(reverse('app:overview'), {'start': '2026-01-01', 'end': '2026-12-31', 'category': 'none'})

    assert response.context['chart_categories'] == [{'name': 'Categoria Não Identificada', 'value': 30.0}]
