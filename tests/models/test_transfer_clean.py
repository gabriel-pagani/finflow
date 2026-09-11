"""Regras de validação da transferência, as que rodam via formulário."""
import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from app.models import Account, BusinessRule, Method, Type


def test_transferencia_valida_passa(make_transfer):
    make_transfer().full_clean()


def test_mesma_conta_nos_dois_lados_e_recusada(make_transfer, account):
    """A conta recebe a regra de entrada para sobrar só a identidade a ser recusada."""
    BusinessRule.objects.create(account=account, type=Type.IN, method=Method.NOT_APPLICABLE)
    with pytest.raises(ValidationError) as erro:
        make_transfer(destination=account).full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_origem_sem_regra_de_saida_e_recusada(make_transfer, db):
    conta = Account.objects.create(description='Caixa')
    with pytest.raises(ValidationError) as erro:
        make_transfer(origin=conta).full_clean()
    assert 'origin' in erro.value.error_dict


def test_destino_sem_regra_de_entrada_e_recusado(make_transfer, db):
    conta = Account.objects.create(description='Caixa')
    with pytest.raises(ValidationError) as erro:
        make_transfer(destination=conta).full_clean()
    assert 'destination' in erro.value.error_dict
