"""Regras de validação da assinatura e do período, as que rodam via formulário."""
from datetime import date

import pytest
from django.core.exceptions import ValidationError

from app.models import Account, BusinessRule, Method, SubscriptionPeriod, Type


def test_assinatura_valida_passa(make_subscription, credit_rule):
    make_subscription().full_clean()


def test_conta_sem_regra_de_credito_e_recusada(make_subscription, db):
    conta = Account.objects.create(description='Caixa')
    BusinessRule.objects.create(account=conta, type=Type.OUT, method=Method.DEBIT)
    with pytest.raises(ValidationError) as erro:
        make_subscription(account=conta).full_clean()
    assert 'account' in erro.value.error_dict


def test_cartao_de_outra_conta_e_recusado(make_subscription, credit_rule, other_account_card):
    with pytest.raises(ValidationError) as erro:
        make_subscription(card=other_account_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_cartao_de_outro_usuario_e_recusado(make_subscription, credit_rule, other_user_card):
    with pytest.raises(ValidationError) as erro:
        make_subscription(card=other_user_card).full_clean()
    assert 'card' in erro.value.error_dict


def test_periodo_valido_passa(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 10))
    SubscriptionPeriod(subscription=assinatura, started_at=date(2026, 6, 10)).full_clean()


def test_encerramento_antes_do_inicio_e_recusado(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 10))
    periodo = SubscriptionPeriod(subscription=assinatura, started_at=date(2026, 6, 10), cancelled_at=date(2026, 5, 10))
    with pytest.raises(ValidationError) as erro:
        periodo.full_clean()
    assert 'cancelled_at' in erro.value.error_dict


def test_segundo_periodo_em_aberto_e_recusado(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10))
    periodo = SubscriptionPeriod(subscription=assinatura, started_at=date(2026, 6, 10))
    with pytest.raises(ValidationError):
        periodo.full_clean()


def test_periodo_sobreposto_e_recusado(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 6, 10))
    periodo = SubscriptionPeriod(subscription=assinatura, started_at=date(2026, 4, 10), cancelled_at=date(2026, 8, 10))
    with pytest.raises(ValidationError) as erro:
        periodo.full_clean()
    assert 'started_at' in erro.value.error_dict
