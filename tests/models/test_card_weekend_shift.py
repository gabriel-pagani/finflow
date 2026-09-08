"""Vencimento que cai em fim de semana é postergado para a segunda-feira."""
from datetime import date

import pytest


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 8, 1), date(2026, 9, 14)),
    (date(2026, 3, 1), date(2026, 4, 13)),
])
def test_vencimento_em_fim_de_semana_vai_para_segunda(make_card, compra, cobranca):
    assert make_card(1, 12).charge_date(compra) == cobranca
    assert cobranca.isoweekday() == 1


def test_vencimento_em_dia_util_nao_se_move(make_card):
    cobranca = make_card(1, 12).charge_date(date(2026, 9, 1))
    assert cobranca == date(2026, 10, 12)
    assert cobranca.isoweekday() == 1
