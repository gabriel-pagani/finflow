"""
O que a exclusão de cada referência do parcelamento permite e o que ela bloqueia.

As parcelas não existem sem o parcelamento que as gerou, então a FK usa CASCADE.
O cartão segue com RESTRICT, como na transação avulsa.
"""
import pytest
from django.db.models.deletion import ProtectedError, RestrictedError

from app.models import Account, Card, Installment, Transaction


def test_apagar_o_parcelamento_leva_junto_as_parcelas(make_installment):
    parcelamento = make_installment()
    parcelamento.save()
    parcelamento.delete()
    assert Transaction.objects.count() == 0


def test_apagar_cartao_com_parcelamento_e_bloqueado(make_installment, card):
    make_installment().save()
    with pytest.raises(RestrictedError):
        card.delete()


def test_apagar_conta_com_parcelamento_e_bloqueado(make_installment, account):
    make_installment().save()
    with pytest.raises(ProtectedError):
        account.delete()
    assert Account.objects.count() == 1


def test_apagar_categoria_com_parcelamento_e_bloqueado(make_installment, category):
    make_installment(category=category).save()
    with pytest.raises(ProtectedError):
        category.delete()


def test_apagar_usuario_leva_junto_parcelamento_cartao_e_parcelas(make_installment, user):
    make_installment().save()
    user.delete()
    assert Installment.objects.count() == 0
    assert Transaction.objects.count() == 0
    assert Card.objects.count() == 0
