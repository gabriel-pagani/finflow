"""
Comportamento de ponta a ponta do cálculo da data de cobrança.

Nenhum teste de charge_date toca no banco: é aritmética de calendário sobre
closing_day e due_day, então basta instanciar o cartão sem salvar. Cada regra
isolada tem seu próprio arquivo nos test_card_* vizinhos.
"""
from datetime import date

import pytest


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 9, 4), date(2026, 9, 14)),
    (date(2026, 9, 11), date(2026, 10, 12)),
])
def test_exemplos_de_referencia(make_card, compra, cobranca):
    """Cartão que fecha dia 5 e vence dia 12, os dois casos usados como especificação."""
    assert make_card(5, 12).charge_date(compra) == cobranca


@pytest.mark.parametrize('closing_day, due_day, compra, cobranca', [
    (5, 12, date(2026, 12, 20), date(2027, 1, 12)),
    (28, 5, date(2026, 12, 29), date(2027, 2, 5)),
    (10, 10, date(2026, 11, 15), date(2027, 1, 11)),
])
def test_virada_de_ano(make_card, closing_day, due_day, compra, cobranca):
    assert make_card(closing_day, due_day).charge_date(compra) == cobranca
