"""
O que a exclusão de cada referência da transferência permite e o que ela bloqueia.

As duas pernas não existem sem a transferência que as gerou, então a FK usa
CASCADE. As contas seguem com PROTECT, como na transação avulsa.
"""
import pytest
from django.db.models.deletion import ProtectedError

from app.models import Account, Transaction, Transfer


def test_apagar_a_transferencia_leva_junto_as_pernas(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    transferencia.delete()
    assert Transaction.objects.count() == 0


def test_apagar_conta_de_origem_e_bloqueado(make_transfer, account):
    make_transfer().save()
    with pytest.raises(ProtectedError):
        account.delete()
    assert Account.objects.count() == 2


def test_apagar_conta_de_destino_e_bloqueado(make_transfer, other_account):
    make_transfer().save()
    with pytest.raises(ProtectedError):
        other_account.delete()


def test_apagar_usuario_leva_junto_transferencia_e_pernas(make_transfer, user):
    make_transfer().save()
    user.delete()
    assert Transfer.objects.count() == 0
    assert Transaction.objects.count() == 0
