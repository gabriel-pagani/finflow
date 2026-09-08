"""
Como a transferência se desdobra no par de transações.

A saída sai da origem em débito e a entrada chega no destino em não se aplica.
Nenhuma das duas recebe categoria: a natureza é interna, e a transação só
aceita categoria em movimento normal.
"""
from datetime import date
from decimal import Decimal

from app.models import BusinessRule, Method, Nature, Type


def test_gera_exatamente_duas_transacoes(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    assert transferencia.transactions.count() == 2


def test_a_saida_sai_da_origem(make_transfer, account):
    transferencia = make_transfer()
    transferencia.save()
    saida = transferencia.transactions.get(type=Type.OUT)
    assert saida.account_id == account.pk
    assert saida.method == Method.DEBIT


def test_a_entrada_chega_no_destino(make_transfer, other_account):
    transferencia = make_transfer()
    transferencia.save()
    entrada = transferencia.transactions.get(type=Type.IN)
    assert entrada.account_id == other_account.pk
    assert entrada.method == Method.NOT_APPLICABLE


def test_as_duas_pernas_tem_o_mesmo_valor_e_a_mesma_data(make_transfer):
    transferencia = make_transfer(value=Decimal('250.00'), occurred_at=date(2026, 9, 4))
    transferencia.save()
    for perna in transferencia.transactions.all():
        assert perna.value == Decimal('250.00')
        assert perna.occurred_at == date(2026, 9, 4)
        assert perna.effective_at == date(2026, 9, 4)


def test_as_pernas_sao_internas_e_sem_categoria(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    for perna in transferencia.transactions.all():
        assert perna.nature == Nature.INTERNAL
        assert perna.category_id is None


def test_salvar_de_novo_nao_duplica_as_pernas(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    transferencia.save()
    assert transferencia.transactions.count() == 2


def test_alterar_a_transferencia_refaz_as_pernas(make_transfer):
    transferencia = make_transfer(value=Decimal('250.00'))
    transferencia.save()
    transferencia.value = Decimal('80.00')
    transferencia.save()
    assert [p.value for p in transferencia.transactions.all()] == [Decimal('80.00'), Decimal('80.00')]


def test_inverter_as_contas_troca_o_lado_das_pernas(make_transfer, account, other_account, db):
    BusinessRule.objects.create(account=other_account, type=Type.OUT, method=Method.DEBIT)
    BusinessRule.objects.create(account=account, type=Type.IN, method=Method.NOT_APPLICABLE)
    transferencia = make_transfer()
    transferencia.save()
    transferencia.origin, transferencia.destination = other_account, account
    transferencia.save()
    assert transferencia.transactions.get(type=Type.OUT).account_id == other_account.pk
    assert transferencia.transactions.get(type=Type.IN).account_id == account.pk
