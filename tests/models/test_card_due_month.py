"""Em que mês cai o vencimento da fatura que fechou."""
from datetime import date


def test_vencimento_posterior_ao_fechamento_cobra_no_mesmo_mes(make_card):
    assert make_card(5, 20).charge_date(date(2026, 3, 1)) == date(2026, 3, 20)


def test_vencimento_anterior_ao_fechamento_cobra_no_mes_seguinte(make_card):
    """Cartão que fecha dia 28 e vence dia 5: a fatura de março só é cobrada em abril."""
    assert make_card(28, 5).charge_date(date(2026, 3, 10)) == date(2026, 4, 6)


def test_vencimento_igual_ao_fechamento_cobra_no_mes_seguinte(make_card):
    assert make_card(10, 10).charge_date(date(2026, 3, 1)) == date(2026, 4, 10)
