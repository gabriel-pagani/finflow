"""Propriedades que valem para qualquer configuração de cartão e qualquer data."""
from calendar import monthrange
from datetime import date, timedelta

import pytest


def todas_as_compras(anos=(2024, 2026)):
    """2024 entra por ser bissexto; 2026 por ser o ano dos exemplos de referência."""
    for ano in anos:
        for mes in range(1, 13):
            for dia in range(1, monthrange(ano, mes)[1] + 1):
                yield date(ano, mes, dia)


@pytest.mark.parametrize('closing_day', range(1, 32))
def test_invariantes_para_todo_vencimento_e_toda_data(make_card, closing_day):
    for due_day in range(1, 32):
        cartao = make_card(closing_day, due_day)
        anterior = None
        for compra in todas_as_compras():
            cobranca = cartao.charge_date(compra)
            config = f'fechamento {closing_day}, vencimento {due_day}, compra {compra}'

            assert cobranca >= compra, f'cobrança antes da compra: {config} -> {cobranca}'
            assert cobranca.isoweekday() <= 5, f'cobrança em fim de semana: {config} -> {cobranca}'
            assert cobranca - compra <= timedelta(days=95), f'prazo irreal: {config} -> {cobranca}'
            if anterior is not None:
                assert cobranca >= anterior, f'ordem quebrada: {config} -> {cobranca} após {anterior}'
            anterior = cobranca
