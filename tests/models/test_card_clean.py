"""O cartão exige que a conta permita saída em crédito."""
import pytest
from django.core.exceptions import ValidationError

from app.models import Card


def novo_cartao(user, account):
    return Card(user=user, account=account, last_digits='1234', closing_day=5, due_day=12)


def test_conta_sem_regra_de_credito_e_recusada(user, account):
    with pytest.raises(ValidationError) as erro:
        novo_cartao(user, account).full_clean()
    assert 'account' in erro.value.error_dict


def test_conta_apenas_com_regra_de_debito_e_recusada(user, account, debit_rule):
    with pytest.raises(ValidationError) as erro:
        novo_cartao(user, account).full_clean()
    assert 'account' in erro.value.error_dict


def test_conta_com_regra_de_credito_e_aceita(user, account, credit_rule):
    novo_cartao(user, account).full_clean()
