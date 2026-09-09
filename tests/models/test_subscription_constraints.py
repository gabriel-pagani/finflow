"""As mesmas regras da assinatura e do período, agora garantidas pelo banco."""
from datetime import date
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from app.models import SubscriptionPeriod, Transaction


@pytest.mark.parametrize('value', [Decimal('0.00'), Decimal('-10.00')])
def test_valor_nao_positivo_e_recusado(make_subscription, value):
    with pytest.raises(IntegrityError):
        make_subscription(value=value).save()


@pytest.mark.parametrize('recurrence', [0, 5, 7])
def test_recorrencia_fora_das_opcoes_e_recusada(make_subscription, recurrence):
    with pytest.raises(IntegrityError):
        make_subscription(recurrence=recurrence).save()


def test_encerramento_antes_do_inicio_e_recusado(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 10))
    with pytest.raises(IntegrityError):
        SubscriptionPeriod.objects.create(
            subscription=assinatura, started_at=date(2026, 6, 10), cancelled_at=date(2026, 5, 10),
        )


def test_segundo_periodo_em_aberto_e_recusado(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10))
    with pytest.raises(IntegrityError):
        SubscriptionPeriod.objects.create(subscription=assinatura, started_at=date(2026, 6, 10))


def test_periodo_repetido_na_mesma_data_e_recusado(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 10))
    with pytest.raises(IntegrityError):
        SubscriptionPeriod.objects.create(
            subscription=assinatura, started_at=date(2026, 1, 10), cancelled_at=date(2026, 4, 10),
        )


def test_periodos_sobrepostos_sao_recusados(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 12, 20))
    with pytest.raises(IntegrityError):
        SubscriptionPeriod.objects.create(
            subscription=assinatura, started_at=date(2026, 3, 10), cancelled_at=date(2026, 6, 20),
        )


def test_periodo_que_encosta_no_ultimo_dia_do_anterior_e_recusado(subscribe, credit_rule):
    """O dia do encerramento ainda pertence ao período: o seguinte não pode começar nele."""
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 12))
    with pytest.raises(IntegrityError):
        SubscriptionPeriod.objects.create(subscription=assinatura, started_at=date(2026, 3, 12))


def test_periodo_seguinte_depois_do_encerramento_e_aceito(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 12))
    SubscriptionPeriod.objects.create(subscription=assinatura, started_at=date(2026, 3, 13))
    assert assinatura.periods.count() == 2


def test_competencia_repetida_na_mesma_assinatura_e_recusada(subscribe, credit_rule):
    assinatura = subscribe()
    cobranca = assinatura.generate_charges(date(2026, 9, 10))[0]
    with pytest.raises(IntegrityError):
        Transaction.objects.create(
            user=assinatura.user, account=assinatura.account, card=assinatura.card,
            type=assinatura.TYPE, method=assinatura.METHOD, value=assinatura.value,
            occurred_at=cobranca.occurred_at, subscription=assinatura, reference=cobranca.reference,
        )


def test_competencia_sem_assinatura_e_recusada(subscribe, credit_rule):
    assinatura = subscribe()
    cobranca = assinatura.generate_charges(date(2026, 9, 10))[0]
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=cobranca.pk).update(subscription=None)


def test_cobranca_e_uma_transacao_comum(subscribe, credit_rule):
    assinatura = subscribe()
    cobranca = assinatura.generate_charges(date(2026, 9, 10))[0]
    assert not cobranca.is_derived
    cobranca.delete()
    assert Transaction.objects.count() == 0
