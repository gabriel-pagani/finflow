"""As mesmas regras da transferência, agora garantidas pelo banco.

Aqui o save() é chamado direto na instância, sem passar por full_clean(): é o
caminho de um script ou de um shell, que o clean() não cobre.
"""
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from app.models import Transaction, Type


@pytest.mark.parametrize('value', [Decimal('0.00'), Decimal('-10.00')])
def test_valor_nao_positivo_e_recusado(make_transfer, value):
    with pytest.raises(IntegrityError):
        make_transfer(value=value).save()


def test_mesma_conta_nos_dois_lados_e_recusada(make_transfer, account):
    with pytest.raises(IntegrityError):
        make_transfer(destination=account).save()


def test_perna_repetida_na_mesma_transferencia_e_recusada(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    entrada = transferencia.transactions.get(type=Type.IN)
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=entrada.pk).update(type=Type.OUT)


def test_transacao_nao_vem_de_duas_origens(make_transfer, make_installment, credit_rule):
    parcelamento = make_installment()
    parcelamento.save()
    transferencia = make_transfer()
    transferencia.save()
    parcela = parcelamento.transactions.first()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=parcela.pk).update(transfer=transferencia)
