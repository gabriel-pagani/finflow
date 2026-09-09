"""
Quando o período cobra: a fase da recorrência, o dia dentro do mês e o corte do
encerramento.

A competência é sempre o primeiro dia do mês cobrado. A fase sai da data de
início do período, então retomar uma assinatura em outro mês recomeça o ciclo
dali — nada é inventado antes do início.
"""
from datetime import date

import pytest

from app.models import Recurrence, SubscriptionPeriod


@pytest.mark.parametrize('started_at, reference, esperada', [
    (date(2026, 9, 10), date(2026, 9, 1), date(2026, 9, 10)),
    (date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 28)),
    (date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29)),
    (date(2026, 1, 31), date(2026, 4, 1), date(2026, 4, 30)),
    (date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 1)),
])
def test_dia_da_cobranca_nunca_sai_do_mes(started_at, reference, esperada):
    assert SubscriptionPeriod(started_at=started_at).charge_date(reference) == esperada


def test_o_dia_encurtado_volta_no_mes_seguinte():
    """Quem cobra dia 31 cai no 28 em fevereiro e volta ao 31 em março."""
    periodo = SubscriptionPeriod(started_at=date(2026, 1, 31))
    assert periodo.due_charge_dates(Recurrence.MONTHLY, date(2026, 4, 30)) == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30),
    ]


@pytest.mark.parametrize('recurrence, esperadas', [
    (Recurrence.MONTHLY, [date(2026, 2, 15), date(2026, 3, 15), date(2026, 4, 15)]),
    (Recurrence.BIMONTHLY, [date(2026, 2, 15), date(2026, 4, 15)]),
    (Recurrence.QUARTERLY, [date(2026, 2, 15)]),
])
def test_a_fase_sai_da_data_de_inicio(recurrence, esperadas):
    periodo = SubscriptionPeriod(started_at=date(2026, 2, 15))
    assert periodo.due_charge_dates(recurrence, date(2026, 4, 30)) == esperadas


def test_nada_vence_antes_do_dia_da_cobranca():
    periodo = SubscriptionPeriod(started_at=date(2026, 9, 10))
    assert periodo.due_charge_dates(Recurrence.MONTHLY, date(2026, 9, 9)) == []
    assert periodo.due_charge_dates(Recurrence.MONTHLY, date(2026, 9, 10)) == [date(2026, 9, 10)]


def test_periodo_que_comeca_no_futuro_nao_vence_nada():
    periodo = SubscriptionPeriod(started_at=date(2026, 12, 1))
    assert periodo.due_charge_dates(Recurrence.MONTHLY, date(2026, 9, 30)) == []


@pytest.mark.parametrize('cancelled_at, ultima', [
    (date(2026, 3, 13), date(2026, 3, 12)),
    (date(2026, 3, 12), date(2026, 3, 12)),
    (date(2026, 3, 11), date(2026, 2, 12)),
])
def test_o_encerramento_ainda_inclui_a_cobranca_do_proprio_dia(cancelled_at, ultima):
    """Cobrando dia 12: encerrar no dia 12 ou depois ainda gera o mês; antes, não."""
    periodo = SubscriptionPeriod(started_at=date(2026, 1, 12), cancelled_at=cancelled_at)
    assert periodo.due_charge_dates(Recurrence.MONTHLY, date(2026, 9, 30))[-1] == ultima


def test_o_encerramento_nao_apaga_o_que_ja_tinha_vencido():
    """Sem acesso desde janeiro: encerrar em março ainda recupera janeiro e fevereiro."""
    periodo = SubscriptionPeriod(started_at=date(2026, 1, 12), cancelled_at=date(2026, 3, 13))
    assert periodo.due_charge_dates(Recurrence.MONTHLY, date(2026, 9, 30)) == [
        date(2026, 1, 12), date(2026, 2, 12), date(2026, 3, 12),
    ]
