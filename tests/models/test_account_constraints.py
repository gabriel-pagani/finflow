"""Unicidade da descrição da conta, garantida pelo banco."""
import pytest
from django.db.utils import IntegrityError

from app.models import Account


def test_descricao_repetida_e_recusada(account):
    with pytest.raises(IntegrityError):
        Account.objects.create(description='Nubank')


def test_descricao_repetida_em_outra_caixa_e_recusada(account):
    with pytest.raises(IntegrityError):
        Account.objects.create(description='NUBANK')


def test_descricoes_diferentes_convivem(account):
    Account.objects.create(description='Inter')
    assert Account.objects.count() == 2
