import pytest
from django.urls import reverse


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


def test_filtro_por_natureza(logged, account, adjustment_rule, make_transaction):
    normal = make_transaction()
    normal.save()
    ajuste = make_transaction(nature='ADJUSTMENT', method='NOT_APPLICABLE')
    ajuste.save()

    response = logged.get(reverse('app:transactions_list'), {'nature': 'ADJUSTMENT', 'start': '2026-01-01', 'end': '2026-12-31'})
    assert list(response.context['object_list']) == [ajuste]
    assert 'Ajuste de Saldo' in response.content.decode()
