"""Unicidade da trinca conta, tipo e método, e tipo e método dentro das opções."""
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


@pytest.mark.parametrize('type', ['', 'TRANSFER'])
def test_tipo_fora_das_opcoes_e_recusado(account, type):
    with pytest.raises(IntegrityError):
        BusinessRule.objects.create(account=account, type=type, method=Method.DEBIT)


@pytest.mark.parametrize('method', ['', 'PIX'])
def test_metodo_fora_das_opcoes_e_recusado(account, method):
    with pytest.raises(IntegrityError):
        BusinessRule.objects.create(account=account, type=Type.OUT, method=method)
