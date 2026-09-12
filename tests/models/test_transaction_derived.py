"""
A transação derivada de um parcelamento ou de uma transferência não se edita
nem se apaga sozinha: quem manda nela é o objeto que a criou.

O bloqueio vive no delete() da instância. A regeneração da origem e o CASCADE
apagam pelo queryset, que não passa por lá — é o caminho que segue aberto.
"""
import pytest
from django.core.exceptions import ValidationError

from app.models import Card, Installment, Transaction, Transfer


def test_parcela_e_derivada(make_installment, credit_rule):
    parcelamento = make_installment()
    parcelamento.save()
    assert parcelamento.transactions.first().is_derived


def test_perna_e_derivada(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    assert transferencia.transactions.first().is_derived


def test_transacao_avulsa_nao_e_derivada(make_transaction):
    assert not make_transaction().is_derived


def test_apagar_parcela_sozinha_e_recusado(make_installment, credit_rule):
    parcelamento = make_installment()
    parcelamento.save()
    with pytest.raises(ValidationError):
        parcelamento.transactions.first().delete()


def test_apagar_perna_sozinha_e_recusado(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    with pytest.raises(ValidationError):
        transferencia.transactions.first().delete()


def test_apagar_transacao_avulsa_e_liberado(make_transaction):
    transacao = make_transaction()
    transacao.save()
    transacao.delete()
    assert Transaction.objects.count() == 0


def test_regenerar_continua_apagando_as_antigas(make_installment, credit_rule):
    parcelamento = make_installment(installments=10)
    parcelamento.save()
    parcelamento.installments = 3
    parcelamento.save()
    assert parcelamento.transactions.count() == 3


def test_apagar_a_origem_continua_levando_as_derivadas(make_installment, make_transfer, credit_rule, user):
    make_installment().save()
    make_transfer().save()
    user.delete()
    assert Transaction.objects.count() == 0
    assert Installment.objects.count() == 0
    assert Transfer.objects.count() == 0
    assert Card.objects.count() == 0
