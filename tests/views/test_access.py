from django.urls import reverse


def test_pagina_exige_login(client):
    response = client.get(reverse('app:transactions_list'))
    assert response.status_code == 302 and 'login' in response['Location']


def test_get_nos_modais_volta_para_a_lista(logged):
    assert logged.get(reverse('app:transaction_create'))['Location'] == reverse('app:transactions_list')
    assert logged.get(reverse('app:card_create'))['Location'] == reverse('app:cards_list')


def test_back_para_fora_do_site_e_ignorado(logged, account, debit_rule):
    response = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT', 'method': 'DEBIT',
        'nature': 'REGULAR', 'value': '10.00', 'back': '//evil.com/',
    })
    assert response['Location'] == reverse('app:transactions_list')
