"""
O que a exclusão de cada referência da assinatura permite e o que ela bloqueia.

Cobrança é despesa que aconteceu: apagar a assinatura não apaga o histórico, e
por isso a FK usa RESTRICT. Não é PROTECT pelo mesmo motivo do cartão — o
RESTRICT abre exceção quando o objeto também cai por um CASCADE na mesma
operação, que é o caso de remover o usuário dono. O período, por não ter valor
sozinho, cai junto da assinatura em CASCADE.
"""
from datetime import date

import pytest
from django.db.models.deletion import ProtectedError, RestrictedError

from app.models import Account, Card, Subscription, SubscriptionPeriod, Transaction


def test_apagar_assinatura_com_cobranca_e_bloqueado(subscribe, credit_rule):
    assinatura = subscribe()
    assinatura.generate_charges(date(2026, 9, 10))
    with pytest.raises(RestrictedError):
        assinatura.delete()
    assert Transaction.objects.count() == 1


def test_apagar_assinatura_sem_cobranca_leva_junto_o_periodo(subscribe, credit_rule):
    subscribe().delete()
    assert Subscription.objects.count() == 0
    assert SubscriptionPeriod.objects.count() == 0


def test_apagar_cartao_com_assinatura_e_bloqueado(subscribe, credit_rule, card):
    subscribe()
    with pytest.raises(RestrictedError):
        card.delete()


def test_apagar_conta_com_assinatura_e_bloqueado(subscribe, credit_rule, account):
    subscribe()
    with pytest.raises(ProtectedError):
        account.delete()
    assert Account.objects.count() == 1


def test_apagar_usuario_leva_junto_assinatura_periodo_cobrancas_e_cartao(subscribe, credit_rule, user):
    assinatura = subscribe()
    assinatura.generate_charges(date(2026, 9, 10))
    user.delete()
    assert Subscription.objects.count() == 0
    assert SubscriptionPeriod.objects.count() == 0
    assert Transaction.objects.count() == 0
    assert Card.objects.count() == 0
