"""
Soma de meses sobre datas, a base do calendário das parcelas.

O dia é encurtado quando o mês de destino é mais curto, e por isso a operação
não tem volta: somar e subtrair um mês não devolve necessariamente a data de
partida.
"""
from datetime import date

import pytest

from app.utils.dates import add_months


@pytest.mark.parametrize('partida, meses, destino', [
    (date(2026, 9, 4), 0, date(2026, 9, 4)),
    (date(2026, 9, 4), 1, date(2026, 10, 4)),
    (date(2026, 9, 4), 12, date(2027, 9, 4)),
])
def test_soma_simples(partida, meses, destino):
    assert add_months(partida, meses) == destino


@pytest.mark.parametrize('partida, meses, destino', [
    (date(2026, 12, 15), 0, date(2026, 12, 15)),
    (date(2026, 12, 15), 1, date(2027, 1, 15)),
    (date(2026, 6, 15), 6, date(2026, 12, 15)),
])
def test_dezembro_nao_vira_o_ano_antes_da_hora(partida, meses, destino):
    """A aritmética é feita em base zero justamente para dezembro não estourar."""
    assert add_months(partida, meses) == destino


@pytest.mark.parametrize('partida, meses, destino', [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2024, 1, 31), 1, date(2024, 2, 29)),
    (date(2026, 3, 31), 1, date(2026, 4, 30)),
])
def test_dia_encurta_no_mes_mais_curto(partida, meses, destino):
    assert add_months(partida, meses) == destino


@pytest.mark.parametrize('partida, meses, destino', [
    (date(2026, 1, 15), -1, date(2025, 12, 15)),
    (date(2026, 1, 15), -13, date(2024, 12, 15)),
])
def test_meses_negativos_voltam_no_calendario(partida, meses, destino):
    assert add_months(partida, meses) == destino
