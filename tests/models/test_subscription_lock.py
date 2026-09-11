"""
Assinatura com cobrança lançada só aceita reajuste de valor.

A cobrança já é despesa registrada: trocar conta, cartão ou recorrência mudaria
o passado sem deixar rastro. O valor escapa da trava porque o reajuste só
alcança as competências que ainda vão ser geradas — as antigas guardam o que
custaram na época.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib import admin as django_admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from app.models import Subscription, SubscriptionPeriod


@pytest.fixture
def com_cobranca(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10))
    assinatura.generate_charges(date(2026, 3, 31))
    assert assinatura.transactions.exists()

    return assinatura


def test_valor_pode_ser_reajustado(com_cobranca):
    com_cobranca.value = Decimal('99.90')
    com_cobranca.full_clean()
    com_cobranca.save()
    assert Subscription.objects.get(pk=com_cobranca.pk).value == Decimal('99.90')


def test_reajuste_nao_mexe_no_que_ja_foi_cobrado(com_cobranca):
    antigos = sorted(c.value for c in com_cobranca.transactions.all())
    com_cobranca.value = Decimal('99.90')
    com_cobranca.save()
    assert sorted(c.value for c in com_cobranca.transactions.all()) == antigos


@pytest.mark.parametrize('campo, valor', [
    ('description', 'Spotify'),
    ('recurrence', 12),
])
def test_campo_travado_nao_muda(com_cobranca, campo, valor):
    setattr(com_cobranca, campo, valor)
    with pytest.raises(ValidationError) as erro:
        com_cobranca.full_clean()
    assert campo in erro.value.error_dict


def test_categoria_travada_nao_muda(com_cobranca, category):
    com_cobranca.category = category
    with pytest.raises(ValidationError) as erro:
        com_cobranca.full_clean()
    assert 'category' in erro.value.error_dict


def test_conta_e_cartao_travados_nao_mudam(com_cobranca, other_account, other_account_card):
    com_cobranca.account = other_account
    com_cobranca.card = other_account_card
    with pytest.raises(ValidationError) as erro:
        com_cobranca.full_clean()
    assert {'account', 'card'} <= set(erro.value.error_dict)


def test_varios_campos_alterados_acusam_todos(com_cobranca):
    com_cobranca.description = 'Spotify'
    com_cobranca.recurrence = 12
    with pytest.raises(ValidationError) as erro:
        com_cobranca.full_clean()
    assert {'description', 'recurrence'} <= set(erro.value.error_dict)


def test_assinatura_sem_cobranca_aceita_qualquer_mudanca(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2030, 1, 10))
    assert not assinatura.transactions.exists()
    assinatura.description = 'Spotify'
    assinatura.recurrence = 12
    assinatura.full_clean()


def test_admin_trava_os_campos_de_quem_ja_tem_cobranca(com_cobranca, admin_user):
    request = RequestFactory().get('/')
    request.user = admin_user
    readonly = django_admin.site.get_model_admin(Subscription).get_readonly_fields(request, com_cobranca)
    assert set(Subscription.LOCKED_AFTER_CHARGES) <= set(readonly)
    assert 'value' not in readonly


def test_periodo_com_cobranca_nao_muda_a_data_de_inicio(com_cobranca):
    periodo = com_cobranca.current_period
    assert periodo.charges().exists()
    periodo.started_at = date(2026, 2, 20)
    with pytest.raises(ValidationError) as erro:
        periodo.full_clean()
    assert 'started_at' in erro.value.error_dict


def test_periodo_com_cobranca_ainda_pode_ser_encerrado(com_cobranca):
    periodo = com_cobranca.current_period
    periodo.cancelled_at = date(2026, 4, 30)
    periodo.full_clean()
    periodo.save()
    assert SubscriptionPeriod.objects.get(pk=periodo.pk).cancelled_at == date(2026, 4, 30)


def test_periodo_nao_encerra_antes_da_ultima_cobranca(com_cobranca):
    periodo = com_cobranca.current_period
    periodo.cancelled_at = date(2026, 3, 9)
    with pytest.raises(ValidationError) as erro:
        periodo.full_clean()
    assert 'cancelled_at' in erro.value.error_dict


def test_periodo_encerra_no_dia_da_ultima_cobranca(com_cobranca):
    periodo = com_cobranca.current_period
    periodo.cancelled_at = date(2026, 3, 10)
    periodo.full_clean()


def test_periodo_encerrado_nao_recua_o_encerramento_para_antes_da_ultima_cobranca(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 3, 20))
    assinatura.generate_charges(date(2026, 6, 30))
    periodo = assinatura.periods.get()
    periodo.cancelled_at = date(2026, 2, 28)
    with pytest.raises(ValidationError) as erro:
        periodo.full_clean()
    assert 'cancelled_at' in erro.value.error_dict


def test_encerramento_retroativo_depois_de_apagar_a_cobranca(com_cobranca):
    com_cobranca.transactions.get(reference=date(2026, 3, 1)).delete()
    periodo = com_cobranca.current_period
    periodo.cancelled_at = date(2026, 3, 9)
    periodo.full_clean()


def test_periodo_sem_cobranca_ainda_muda_a_data_de_inicio(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2030, 1, 10))
    periodo = assinatura.current_period
    assert not periodo.charges().exists()
    periodo.started_at = date(2030, 2, 20)
    periodo.full_clean()
    periodo.save()
    assert SubscriptionPeriod.objects.get(pk=periodo.pk).started_at == date(2030, 2, 20)


def test_periodo_com_cobranca_nao_pode_ser_apagado(com_cobranca):
    periodo = com_cobranca.current_period
    with pytest.raises(ValidationError):
        periodo.delete()
    assert SubscriptionPeriod.objects.filter(pk=periodo.pk).exists()


def test_exclusao_do_periodo_olha_as_datas_gravadas(com_cobranca):
    periodo = com_cobranca.current_period
    periodo.started_at = date(2030, 1, 10)
    assert not periodo.charges().exists()
    with pytest.raises(ValidationError):
        periodo.delete()
    assert SubscriptionPeriod.objects.filter(pk=periodo.pk).exists()


def test_periodo_sem_cobranca_pode_ser_apagado(subscribe, make_period, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 2, 15))
    futuro = make_period(assinatura, started_at=date(2030, 1, 10))
    assinatura.generate_charges(date(2026, 3, 31))
    assert not futuro.charges().exists()
    futuro.delete()
    assert list(assinatura.periods.values_list('started_at', flat=True)) == [date(2026, 1, 10)]


def formset_excluindo(assinatura, periodo, admin_user):
    request = RequestFactory().post('/')
    request.user = admin_user
    model_admin = django_admin.site.get_model_admin(Subscription)
    inline = next(i for i in model_admin.get_inline_instances(request, assinatura) if i.model is SubscriptionPeriod)
    data = {
        'periods-TOTAL_FORMS': '1',
        'periods-INITIAL_FORMS': '1',
        'periods-0-id': str(periodo.pk),
        'periods-0-subscription': str(assinatura.pk),
        'periods-0-started_at': periodo.started_at.isoformat(),
        'periods-0-cancelled_at': periodo.cancelled_at.isoformat() if periodo.cancelled_at else '',
        'periods-0-DELETE': 'on',
    }

    return inline.get_formset(request, assinatura)(data, instance=assinatura)


def test_admin_recusa_apagar_periodo_com_cobranca(com_cobranca, admin_user):
    formset = formset_excluindo(com_cobranca, com_cobranca.current_period, admin_user)
    assert not formset.is_valid()
    assert 'cobranças lançadas' in formset.non_form_errors()[0]


def test_admin_apaga_periodo_sem_cobranca(subscribe, credit_rule, admin_user):
    assinatura = subscribe(started_at=date(2030, 1, 10))
    formset = formset_excluindo(assinatura, assinatura.current_period, admin_user)
    assert formset.is_valid(), formset.non_form_errors()
    formset.save()
    assert not assinatura.periods.exists()


def test_cada_periodo_reconhece_as_proprias_cobrancas(subscribe, make_period, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 2, 15))
    make_period(assinatura, started_at=date(2026, 5, 10))
    assinatura.generate_charges(date(2026, 6, 30))

    primeiro, segundo = assinatura.periods.order_by('started_at')
    assert [c.reference for c in primeiro.charges().order_by('reference')] == [
        date(2026, 1, 1), date(2026, 2, 1),
    ]
    assert [c.reference for c in segundo.charges().order_by('reference')] == [
        date(2026, 5, 1), date(2026, 6, 1),
    ]
