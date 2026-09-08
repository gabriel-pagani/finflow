"""
O que a exclusão de cada referência da transação permite e o que ela bloqueia.

Cartão usa RESTRICT e não PROTECT: os dois barram apagar um cartão que tem
transações, mas o RESTRICT abre exceção quando a transação também está sendo
apagada na mesma operação por um CASCADE — o caso de remover o usuário dono.
"""
import pytest
from django.db.models.deletion import ProtectedError, RestrictedError

from app.models import Account, Card, Method, Transaction


def test_apagar_cartao_com_transacao_e_bloqueado(make_transaction, card):
    make_transaction(method=Method.CREDIT, card=card).save()
    with pytest.raises(RestrictedError):
        card.delete()


def test_apagar_cartao_sem_transacao_e_liberado(card):
    card.delete()
    assert Card.objects.count() == 0


def test_apagar_usuario_leva_junto_cartao_e_transacao(make_transaction, card, user):
    make_transaction(method=Method.CREDIT, card=card).save()
    user.delete()
    assert Transaction.objects.count() == 0
    assert Card.objects.count() == 0


def test_apagar_conta_com_transacao_e_bloqueado(make_transaction, account):
    make_transaction().save()
    with pytest.raises(ProtectedError):
        account.delete()
    assert Account.objects.count() == 1
