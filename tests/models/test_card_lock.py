"""Cartão com transação registrada não troca de dono, de conta nem de final."""
import pytest
from django.contrib import admin as django_admin
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import RequestFactory

from app.models import Card, Method


@pytest.fixture
def com_transacao(card, make_transaction):
    make_transaction(method=Method.CREDIT, card=card).save()
    assert card.transactions.exists()

    return card


@pytest.mark.parametrize('campo', ['user', 'account', 'last_digits'])
def test_campo_travado_nao_muda(com_transacao, other_user, other_account_card, campo):
    novo = {'user': other_user, 'account': other_account_card.account, 'last_digits': '4321'}[campo]
    setattr(com_transacao, campo, novo)
    with pytest.raises(ValidationError) as erro:
        com_transacao.full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


def test_cartao_sem_mudanca_passa(com_transacao):
    com_transacao.full_clean()


def test_ciclo_ainda_pode_ser_ajustado(com_transacao):
    com_transacao.closing_day = 10
    com_transacao.due_day = 20
    com_transacao.full_clean()


def test_cartao_sem_transacao_aceita_qualquer_mudanca(card, other_user):
    card.user = other_user
    card.last_digits = '4321'
    card.full_clean()


def test_admin_trava_os_campos_de_quem_ja_tem_transacao(com_transacao, admin_user):
    request = RequestFactory().get('/')
    request.user = admin_user
    readonly = django_admin.site.get_model_admin(Card).get_readonly_fields(request, com_transacao)
    assert set(Card.LOCKED_AFTER_TRANSACTIONS) <= set(readonly)


def test_admin_libera_os_campos_de_quem_nao_tem_transacao(card, admin_user):
    request = RequestFactory().get('/')
    request.user = admin_user
    readonly = django_admin.site.get_model_admin(Card).get_readonly_fields(request, card)
    assert not set(Card.LOCKED_AFTER_TRANSACTIONS) & set(readonly)
