"""Cada regra dos models recusada pela tela vira mensagem, e não erro 500.

O `full_clean` que o ModelForm dispara pula toda constraint que cite um campo
fora do formulário, e o que passa batido só estoura no INSERT. Estes testes
percorrem as regras alcançáveis por POST para garantir que nenhuma escapa.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from app.models import BusinessRule, Card, Installment, Method, Transaction, Transfer, Type


def post(client, route, *args, **fields):
    return client.post(reverse(route, args=args), fields, follow=True)


def messages(response):
    return [str(message) for message in response.context['messages']]


def transaction_payload(account, **fields):
    return {'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT',
            'method': 'DEBIT', 'nature': 'REGULAR', 'value': '10.00', **fields}


@pytest.mark.parametrize('value, expected', [
    ('0.00', 'O valor deve ser maior que zero.'),
    ('-10.00', 'O valor deve ser maior que zero.'),
])
def test_valor_nao_positivo(logged, account, debit_rule, value, expected):
    response = post(logged, 'app:transaction_create', **transaction_payload(account, value=value))

    assert not Transaction.objects.exists()
    assert expected in messages(response)


def test_interna_no_credito_e_recusada(logged, account, card, credit_rule):
    response = post(logged, 'app:transaction_create', **transaction_payload(
        account, nature='INTERNAL', method='CREDIT', card=card.pk))

    assert not Transaction.objects.exists()
    assert 'A natureza Interna só mexe no saldo e nunca é em Crédito.' in messages(response)


def test_interna_nao_recebe_categoria(logged, account, category, debit_rule):
    response = post(logged, 'app:transaction_create', **transaction_payload(account, nature='INTERNAL', category=category.pk))

    assert not Transaction.objects.exists()
    assert 'Apenas transações com natureza Normal recebem categoria.' in messages(response)


def test_interna_avulsa_valida_passa(logged, account, debit_rule):
    post(logged, 'app:transaction_create', **transaction_payload(account, nature='INTERNAL'))

    assert Transaction.objects.get().nature == 'INTERNAL'


def test_cartao_fora_do_credito_e_descartado(logged, account, card, credit_rule, debit_rule):
    post(logged, 'app:transaction_create', **transaction_payload(account, method='DEBIT', card=card.pk))

    assert Transaction.objects.get().card_id is None


def test_cartao_de_outra_conta(logged, account, other_account_card, credit_rule):
    response = post(logged, 'app:transaction_create', **transaction_payload(
        account, method='CREDIT', card=other_account_card.pk))

    assert not Transaction.objects.exists()
    assert 'O cartão escolhido pertence a outra conta.' in messages(response)


@pytest.mark.parametrize('installments, expected', [
    ('1', 'Um parcelamento deve ter de 2 a 360 parcelas.'),
    ('361', 'Um parcelamento deve ter de 2 a 360 parcelas.'),
])
def test_numero_de_parcelas_fora_da_faixa(logged, account, card, credit_rule, installments, expected):
    response = post(logged, 'app:installment_create', occurred_at='2026-09-04', account=account.pk,
                    card=card.pk, value='300.00', installments=installments)

    assert not Installment.objects.exists()
    assert expected in messages(response)


def test_valor_que_nao_cobre_as_parcelas(logged, account, card, credit_rule):
    response = post(logged, 'app:installment_create', occurred_at='2026-09-04', account=account.pk,
                    card=card.pk, value='0.02', installments='3')

    assert not Installment.objects.exists()
    assert 'O valor deve dar ao menos um centavo para cada parcela.' in messages(response)


def test_parcelamento_sem_cartao(logged, account, credit_rule):
    response = post(logged, 'app:installment_create', occurred_at='2026-09-04', account=account.pk,
                    value='300.00', installments='3')

    assert not Installment.objects.exists()
    assert any('cartão' in message.lower() for message in messages(response))


def test_transferencia_para_a_mesma_conta(logged, account, transfer_out_rule):
    BusinessRule.objects.create(account=account, type=Type.IN, method=Method.NOT_APPLICABLE)

    response = post(logged, 'app:transfer_create', occurred_at='2026-09-04',
                    origin=account.pk, destination=account.pk, value='50.00')

    assert not Transfer.objects.exists()
    assert 'A conta de destino deve ser diferente da conta de origem.' in messages(response)


def test_transferencia_com_valor_zerado(logged, account, other_account, transfer_out_rule, transfer_in_rule):
    response = post(logged, 'app:transfer_create', occurred_at='2026-09-04',
                    origin=account.pk, destination=other_account.pk, value='0.00')

    assert not Transfer.objects.exists()
    assert 'O valor deve ser maior que zero.' in messages(response)


def test_conta_de_destino_sem_regra_de_entrada(logged, account, other_account, transfer_out_rule):
    response = post(logged, 'app:transfer_create', occurred_at='2026-09-04',
                    origin=account.pk, destination=other_account.pk, value='50.00')

    assert not Transfer.objects.exists()
    assert any('destino não permite entrada' in message for message in messages(response))


@pytest.mark.parametrize('fields, expected', [
    ({'closing_day': '0'}, 'Os dias de fechamento e vencimento devem estar entre 1 e 31.'),
    ({'due_day': '32'}, 'Os dias de fechamento e vencimento devem estar entre 1 e 31.'),
])
def test_dias_do_ciclo_fora_do_mes(logged, account, credit_rule, fields, expected):
    payload = {'account': account.pk, 'last_digits': '4321', 'closing_day': '5', 'due_day': '12', **fields}
    response = post(logged, 'app:card_create', **payload)

    assert not Card.objects.exists()
    assert expected in messages(response)


def test_final_do_cartao_com_letra(logged, account, credit_rule):
    response = post(logged, 'app:card_create', account=account.pk, last_digits='12a4',
                    closing_day='5', due_day='12')

    assert not Card.objects.exists()
    assert 'Informe exatamente os últimos quatro dígitos do cartão.' in messages(response)


def test_conta_sem_regra_de_credito_nao_recebe_cartao(logged, account, debit_rule):
    response = post(logged, 'app:card_create', account=account.pk, last_digits='4321',
                    closing_day='5', due_day='12')

    assert not Card.objects.exists()
    assert any('não permite saída em Crédito' in message for message in messages(response))


def test_cartao_com_transacoes_nao_troca_de_final(logged, card, account, credit_rule, make_transaction):
    make_transaction(card=card, method='CREDIT').save()

    response = post(logged, 'app:card_update', card.pk, account=account.pk, last_digits='9999',
                    closing_day=card.closing_day, due_day=card.due_day)

    card.refresh_from_db()
    assert card.last_digits == '1234'
    assert any('apenas as datas de fechamento e vencimento' in message for message in messages(response))


def test_parcelamento_gera_parcelas_que_o_banco_aceita(logged, account, card, credit_rule):
    post(logged, 'app:installment_create', occurred_at='2026-09-04', account=account.pk,
         card=card.pk, value='0.03', installments='3')

    parcels = Transaction.objects.order_by('parcel')
    assert [parcel.value for parcel in parcels] == [Decimal('0.01')] * 3


def test_transferencia_mostra_os_dois_lados_errados_de_uma_vez(logged, account, other_account):
    response = post(logged, 'app:transfer_create', occurred_at='2026-09-04',
                    origin=account.pk, destination=other_account.pk, value='50.00')

    assert not Transfer.objects.exists()
    assert sorted(messages(response)) == sorted([
        'A conta de origem não permite saída em Débito, necessário para registrar a transferência.',
        'A conta de destino não permite entrada em Não Se Aplica, necessário para registrar a transferência.',
    ])


def test_parcelamento_mostra_conta_e_cartao_errados_de_uma_vez(logged, other_account, card, credit_rule):
    response = post(logged, 'app:installment_create', occurred_at='2026-09-04',
                    account=other_account.pk, card=card.pk, value='300.00', installments='3')

    assert not Installment.objects.exists()
    assert sorted(messages(response)) == sorted([
        'A conta não permite saída em Crédito, necessário para registrar as parcelas.',
        'O cartão escolhido pertence a outra conta.',
    ])


def test_transacao_mostra_natureza_e_regra_erradas_de_uma_vez(logged, account, card, credit_rule):
    response = post(logged, 'app:transaction_create', **transaction_payload(
        account, type='IN', method='CREDIT', card=card.pk, nature='INTERNAL'))

    assert not Transaction.objects.exists()
    assert len(messages(response)) == 2
