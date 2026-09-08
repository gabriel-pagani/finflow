"""Em qual fatura a compra entra: o corte acontece no dia do fechamento."""
from datetime import date


def test_compra_antes_do_fechamento_entra_na_fatura_do_mes(make_card):
    assert make_card(5, 20).charge_date(date(2026, 9, 4)) == date(2026, 9, 21)


def test_compra_no_dia_do_fechamento_entra_na_fatura_seguinte(make_card):
    assert make_card(5, 20).charge_date(date(2026, 9, 5)) == date(2026, 10, 20)


def test_corte_acontece_exatamente_no_dia_do_fechamento(make_card):
    cartao = make_card(5, 20)
    anteriores = {cartao.charge_date(date(2026, 9, dia)) for dia in range(1, 5)}
    posteriores = {cartao.charge_date(date(2026, 9, dia)) for dia in range(5, 31)}
    assert anteriores == {date(2026, 9, 21)}
    assert posteriores == {date(2026, 10, 20)}


def test_fim_de_semana_nao_adia_o_fechamento(make_card):
    """O empurrão de fim de semana vale para o vencimento, nunca para o corte."""
    assert date(2026, 9, 5).isoweekday() == 6
    assert make_card(5, 12).charge_date(date(2026, 9, 5)) == date(2026, 10, 12)
