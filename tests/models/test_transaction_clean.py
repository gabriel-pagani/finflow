"""Regras de validação da transação, as que rodam via formulário."""
import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from app.models import BusinessRule, Method, Nature, Type


def test_transacao_valida_passa(make_transaction):
    make_transaction().full_clean()


def test_combinacao_fora_das_regras_de_negocio_e_recusada(make_transaction):
    with pytest.raises(ValidationError):
        make_transaction(method=Method.NOT_APPLICABLE).full_clean()


def test_credito_sem_cartao_e_recusado(make_transaction, credit_rule):
    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT).full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_cartao_fora_do_credito_e_recusado(make_transaction, card):
    with pytest.raises(ValidationError) as erro:
        make_transaction(card=card).full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_cartao_de_outra_conta_e_recusado(make_transaction, credit_rule, other_account_card):
    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT, card=other_account_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_cartao_de_outro_usuario_e_recusado(make_transaction, credit_rule, other_user_card):
    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT, card=other_user_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_categoria_fora_da_natureza_normal_e_recusada(make_transaction, account, category):
    BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.NOT_APPLICABLE)
    with pytest.raises(ValidationError) as erro:
        make_transaction(nature=Nature.ADJUSTMENT, method=Method.NOT_APPLICABLE, category=category).full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_categoria_na_natureza_normal_e_aceita(make_transaction, category):
    make_transaction(category=category).full_clean()


def test_parcela_fora_da_natureza_normal_e_recusada(make_installment, credit_rule):
    parcelamento = make_installment()
    parcelamento.save()
    parcela = parcelamento.transactions.first()
    parcela.nature = Nature.INTERNAL
    with pytest.raises(ValidationError) as erro:
        parcela.full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_perna_de_transferencia_fora_da_natureza_interna_e_recusada(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    perna = transferencia.transactions.first()
    perna.nature = Nature.REGULAR
    with pytest.raises(ValidationError) as erro:
        perna.full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict
