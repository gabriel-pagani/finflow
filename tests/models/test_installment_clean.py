"""Regras de validação do parcelamento, as que rodam via formulário."""
from decimal import Decimal

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from app.models import Account, BusinessRule, Method, Type


def test_parcelamento_valido_passa(make_installment, credit_rule):
    make_installment().full_clean()


def test_conta_sem_regra_de_credito_e_recusada(make_installment, db):
    conta = Account.objects.create(description='Caixa')
    BusinessRule.objects.create(account=conta, type=Type.OUT, method=Method.DEBIT)
    with pytest.raises(ValidationError) as erro:
        make_installment(account=conta).full_clean()
    assert 'account' in erro.value.error_dict


@pytest.mark.parametrize('installments', [0, 1, 361])
def test_numero_de_parcelas_fora_da_faixa_e_recusado(make_installment, credit_rule, installments):
    with pytest.raises(ValidationError) as erro:
        make_installment(installments=installments).full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


@pytest.mark.parametrize('installments', [2, 360])
def test_extremos_da_faixa_sao_aceitos(make_installment, credit_rule, installments):
    make_installment(installments=installments).full_clean()


def test_valor_que_zera_uma_parcela_e_recusado(make_installment, credit_rule):
    """R$ 1,00 em 101x daria parcelas de zero, que a transação não aceitaria."""
    with pytest.raises(ValidationError) as erro:
        make_installment(value=Decimal('1.00'), installments=101).full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_valor_que_cobre_exatamente_um_centavo_por_parcela_e_aceito(make_installment, credit_rule):
    make_installment(value=Decimal('1.00'), installments=100).full_clean()


def test_cartao_de_outra_conta_e_recusado(make_installment, credit_rule, other_account_card):
    with pytest.raises(ValidationError) as erro:
        make_installment(card=other_account_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_cartao_de_outro_usuario_e_recusado(make_installment, credit_rule, other_user_card):
    with pytest.raises(ValidationError) as erro:
        make_installment(card=other_user_card).full_clean()
    assert 'card' in erro.value.error_dict
