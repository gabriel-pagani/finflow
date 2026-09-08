"""Dias do ciclo que não existem no mês, fevereiro de ano bissexto incluído."""
from datetime import date

import pytest


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 2, 27), date(2026, 3, 10)),
    (date(2026, 2, 28), date(2026, 4, 10)),
    (date(2026, 4, 30), date(2026, 6, 10)),
])
def test_fechamento_maior_que_o_mes_cai_no_ultimo_dia(make_card, compra, cobranca):
    """Fechamento dia 31 fecha no dia 28, 29 ou 30 nos meses que não têm dia 31."""
    assert make_card(31, 10).charge_date(compra) == cobranca


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 2, 10), date(2026, 3, 2)),
    (date(2026, 3, 10), date(2026, 3, 31)),
])
def test_vencimento_maior_que_o_mes_cai_no_ultimo_dia(make_card, compra, cobranca):
    """Vencimento dia 31 vira dia 28 em fevereiro de 2026, que é sábado e vai para segunda."""
    assert make_card(20, 31).charge_date(compra) == cobranca


@pytest.mark.parametrize('compra, cobranca', [
    (date(2024, 2, 28), date(2024, 3, 11)),
    (date(2024, 2, 29), date(2024, 4, 10)),
])
def test_ano_bissexto_fecha_no_dia_29(make_card, compra, cobranca):
    assert make_card(31, 10).charge_date(compra) == cobranca
