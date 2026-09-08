"""
Como o parcelamento se desdobra em transações.

O cartão de referência fecha dia 5 e vence dia 12, o mesmo do conftest, então as
datas efetivas esperadas coincidem com as de test_card_charge_date.py.
"""
from datetime import date
from decimal import Decimal

import pytest


def test_gera_uma_transacao_por_parcela(make_installment):
    parcelamento = make_installment(installments=3)
    parcelamento.save()
    assert parcelamento.transactions.count() == 3
    assert [t.parcel for t in parcelamento.transactions.order_by('parcel')] == [1, 2, 3]


def test_a_soma_das_parcelas_fecha_o_valor_total(make_installment):
    parcelamento = make_installment(value=Decimal('1000.00'), installments=3)
    parcelamento.save()
    assert sum(t.value for t in parcelamento.transactions.all()) == Decimal('1000.00')


@pytest.mark.parametrize('value, installments, esperado', [
    (Decimal('1000.00'), 3, [Decimal('333.33'), Decimal('333.33'), Decimal('333.34')]),
    (Decimal('100.00'), 7, [Decimal('14.28')] * 6 + [Decimal('14.32')]),
    (Decimal('500.00'), 2, [Decimal('250.00'), Decimal('250.00')]),
])
def test_a_sobra_dos_centavos_cai_na_ultima_parcela(make_installment, value, installments, esperado):
    parcelamento = make_installment(value=value, installments=installments)
    parcelamento.save()
    assert [t.value for t in parcelamento.transactions.order_by('parcel')] == esperado


def test_cada_parcela_ocorre_um_mes_depois_da_anterior(make_installment):
    parcelamento = make_installment(occurred_at=date(2026, 9, 4), installments=3)
    parcelamento.save()
    assert [t.occurred_at for t in parcelamento.transactions.order_by('parcel')] == [
        date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 4),
    ]


def test_cada_parcela_cai_em_uma_fatura(make_installment):
    parcelamento = make_installment(occurred_at=date(2026, 9, 4), installments=3)
    parcelamento.save()
    assert [t.effective_at for t in parcelamento.transactions.order_by('parcel')] == [
        date(2026, 9, 14), date(2026, 10, 12), date(2026, 11, 12),
    ]


def test_as_parcelas_herdam_os_dados_da_compra(make_installment, category):
    parcelamento = make_installment(category=category, description='Notebook')
    parcelamento.save()
    for parcela in parcelamento.transactions.all():
        assert parcela.user_id == parcelamento.user_id
        assert parcela.account_id == parcelamento.account_id
        assert parcela.card_id == parcelamento.card_id
        assert parcela.category_id == parcelamento.category_id
        assert parcela.description == 'Notebook'
        assert parcela.type == parcelamento.TYPE
        assert parcela.method == parcelamento.METHOD
        assert parcela.nature == parcelamento.NATURE


def test_salvar_de_novo_nao_muda_as_parcelas(make_installment):
    """A geração parte sempre da data da compra, então é idempotente."""
    parcelamento = make_installment()
    parcelamento.save()
    antes = [(t.parcel, t.value, t.occurred_at, t.effective_at)
             for t in parcelamento.transactions.order_by('parcel')]
    parcelamento.save()
    depois = [(t.parcel, t.value, t.occurred_at, t.effective_at)
              for t in parcelamento.transactions.order_by('parcel')]
    assert antes == depois


def test_alterar_o_parcelamento_refaz_as_parcelas(make_installment):
    parcelamento = make_installment(value=Decimal('1000.00'), installments=10)
    parcelamento.save()
    parcelamento.value = Decimal('500.00')
    parcelamento.installments = 2
    parcelamento.save()
    parcelas = parcelamento.transactions.order_by('parcel')
    assert [t.parcel for t in parcelas] == [1, 2]
    assert [t.value for t in parcelas] == [Decimal('250.00'), Decimal('250.00')]


def test_compra_no_fim_do_mes_encurta_o_dia_das_parcelas(make_installment):
    parcelamento = make_installment(occurred_at=date(2026, 1, 31), installments=3)
    parcelamento.save()
    assert [t.occurred_at for t in parcelamento.transactions.order_by('parcel')] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
    ]
