"""
As combinações que os modais oferecem antes de qualquer envio.

A filtragem em si acontece no navegador; aqui se garante que ele receba os
dados certos para fazê-la. O que escapar dela continua esbarrando na validação
do model, testada em test_form_rules.py.
"""
import pytest
from django.urls import reverse


def options(client):
    return client.get(reverse('app:transactions_list')).context['form_options']


def test_regras_saem_agrupadas_por_conta_e_tipo(logged, account, other_account, credit_rule, debit_rule, transfer_in_rule):
    rules = options(logged)['rules']

    assert rules[str(account.pk)] == {'OUT': ['CREDIT', 'DEBIT']}
    assert rules[str(other_account.pk)] == {'IN': ['NOT_APPLICABLE']}


def test_conta_sem_regra_nao_aparece(logged, account, debit_rule, other_account):
    assert str(other_account.pk) not in options(logged)['rules']


def test_cartao_sai_com_a_conta_dona(logged, card, other_account_card):
    cards = options(logged)['cards']

    assert cards == {
        str(card.pk): str(card.account_id),
        str(other_account_card.pk): str(other_account_card.account_id),
    }


def test_cartao_de_outro_usuario_fica_de_fora(logged, card, other_user_card):
    assert str(other_user_card.pk) not in options(logged)['cards']


def test_natureza_sai_recortada_pelo_metodo(logged):
    """Investimento nunca no crédito, ajuste de saldo só em Não Se Aplica."""
    assert options(logged)['natures'] == {
        'CREDIT': ['REGULAR'],
        'DEBIT': ['REGULAR', 'INVESTMENT'],
        'NOT_APPLICABLE': ['REGULAR', 'ADJUSTMENT', 'INVESTMENT'],
    }


def test_formularios_sem_tipo_e_metodo_fixam_a_combinacao_de_cada_conta(logged):
    """Sem tipo e método no formulário, é o que recorta as contas oferecidas."""
    assert options(logged)['fixed'] == {
        'installment': {'account': {'type': 'OUT', 'method': 'CREDIT'}},
        'card': {'account': {'type': 'OUT', 'method': 'CREDIT'}},
        'transfer': {
            'origin': {'type': 'OUT', 'method': 'DEBIT'},
            'destination': {'type': 'IN', 'method': 'NOT_APPLICABLE'},
        },
    }


def test_so_cartao_com_lancamento_sai_travado(logged, card, other_account_card, make_transaction, credit_rule):
    make_transaction(method='CREDIT', card=card).save()
    make_transaction(method='CREDIT', card=card, value='20.00').save()

    assert options(logged)['locked_cards'] == [str(card.pk)]


@pytest.mark.parametrize('route', ['app:transactions_list', 'app:cards_list'])
def test_pagina_entrega_as_opcoes_ao_navegador(logged, route, card, debit_rule):
    page = logged.get(reverse(route)).content.decode()

    assert 'id="data-form-options"' in page
    # json_script escapa o conteúdo; sem isso a descrição de uma conta poderia
    # fechar o <script> antes da hora.
    assert '<script id="data-form-options" type="application/json">' in page
