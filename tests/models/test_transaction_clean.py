"""Regras de validação da transação, as que rodam via formulário."""
import pytest
from django.core.exceptions import ValidationError

from app.models import Method, Nature


def test_transacao_valida_passa(make_transaction):
    make_transaction().full_clean()


def test_combinacao_fora_das_regras_de_negocio_e_recusada(make_transaction):
    with pytest.raises(ValidationError):
        make_transaction(method=Method.NOT_APPLICABLE).full_clean()


def test_credito_sem_cartao_e_recusado(make_transaction, credit_rule):
    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT).full_clean()
    assert 'card' in erro.value.error_dict


def test_cartao_fora_do_credito_e_recusado(make_transaction, card):
    with pytest.raises(ValidationError) as erro:
        make_transaction(card=card).full_clean()
    assert 'card' in erro.value.error_dict


def test_cartao_de_outra_conta_e_recusado(make_transaction, credit_rule, other_account_card):
    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT, card=other_account_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_cartao_de_outro_usuario_e_recusado(make_transaction, credit_rule, other_user_card):
    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT, card=other_user_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_categoria_fora_da_natureza_normal_e_recusada(make_transaction, category):
    with pytest.raises(ValidationError) as erro:
        make_transaction(nature=Nature.INTERNAL, category=category).full_clean()
    assert 'category' in erro.value.error_dict


def test_categoria_na_natureza_normal_e_aceita(make_transaction, category):
    make_transaction(category=category).full_clean()
