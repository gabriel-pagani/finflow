"""Unicidade da trinca conta, tipo e método."""
import pytest
from django.db.utils import IntegrityError

from app.models import BusinessRule, Method, Type


def test_combinacao_repetida_e_recusada(account, credit_rule):
    with pytest.raises(IntegrityError):
        BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.CREDIT)


def test_mesma_conta_com_outro_metodo_e_aceita(account, credit_rule):
    BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)
    assert BusinessRule.objects.count() == 2


def test_mesmo_metodo_em_outro_tipo_e_aceito(account, credit_rule):
    BusinessRule.objects.create(account=account, type=Type.IN, method=Method.CREDIT)
    assert BusinessRule.objects.count() == 2
