"""
Testes do cálculo da data de cobrança de uma compra no cartão.

Nenhum teste aqui toca no banco: `Card.charge_date` é aritmética de calendário
sobre `closing_day` e `due_day`, então basta instanciar o cartão sem salvar.
"""
from calendar import monthrange
from datetime import date, timedelta

import pytest

from app.models import Card


def card(closing_day, due_day):
    return Card(closing_day=closing_day, due_day=due_day)


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 9, 4), date(2026, 9, 14)),
    (date(2026, 9, 11), date(2026, 10, 12)),
])
def test_exemplos_de_referencia(compra, cobranca):
    """Cartão que fecha dia 5 e vence dia 12, os dois casos usados como especificação."""
    assert card(5, 12).charge_date(compra) == cobranca


def test_compra_antes_do_fechamento_entra_na_fatura_do_mes():
    assert card(5, 20).charge_date(date(2026, 9, 4)) == date(2026, 9, 21)


def test_compra_no_dia_do_fechamento_entra_na_fatura_seguinte():
    assert card(5, 20).charge_date(date(2026, 9, 5)) == date(2026, 10, 20)


def test_corte_acontece_exatamente_no_dia_do_fechamento():
    cartao = card(5, 20)
    anteriores = {cartao.charge_date(date(2026, 9, dia)) for dia in range(1, 5)}
    posteriores = {cartao.charge_date(date(2026, 9, dia)) for dia in range(5, 31)}
    assert anteriores == {date(2026, 9, 21)}
    assert posteriores == {date(2026, 10, 20)}


def test_fim_de_semana_nao_adia_o_fechamento():
    """O empurrão de fim de semana vale para o vencimento, nunca para o corte da fatura."""
    assert date(2026, 9, 5).isoweekday() == 6
    assert card(5, 12).charge_date(date(2026, 9, 5)) == date(2026, 10, 12)


def test_vencimento_posterior_ao_fechamento_cobra_no_mesmo_mes():
    assert card(5, 20).charge_date(date(2026, 3, 1)) == date(2026, 3, 20)


def test_vencimento_anterior_ao_fechamento_cobra_no_mes_seguinte():
    """Cartão que fecha dia 28 e vence dia 5: a fatura de março só é cobrada em abril."""
    assert card(28, 5).charge_date(date(2026, 3, 10)) == date(2026, 4, 6)


def test_vencimento_igual_ao_fechamento_cobra_no_mes_seguinte():
    assert card(10, 10).charge_date(date(2026, 3, 1)) == date(2026, 4, 10)


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 2, 27), date(2026, 3, 10)),
    (date(2026, 2, 28), date(2026, 4, 10)),
    (date(2026, 4, 30), date(2026, 6, 10)),
])
def test_fechamento_maior_que_o_mes_cai_no_ultimo_dia(compra, cobranca):
    """Fechamento dia 31 fecha no dia 28, 29 ou 30 nos meses que não têm dia 31."""
    assert card(31, 10).charge_date(compra) == cobranca


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 2, 10), date(2026, 3, 2)),
    (date(2026, 3, 10), date(2026, 3, 31)),
])
def test_vencimento_maior_que_o_mes_cai_no_ultimo_dia(compra, cobranca):
    """Vencimento dia 31 vira dia 28 em fevereiro de 2026, que é sábado e vai para segunda."""
    assert card(20, 31).charge_date(compra) == cobranca


@pytest.mark.parametrize('compra, cobranca', [
    (date(2024, 2, 28), date(2024, 3, 11)),
    (date(2024, 2, 29), date(2024, 4, 10)),
])
def test_ano_bissexto_fecha_no_dia_29(compra, cobranca):
    assert card(31, 10).charge_date(compra) == cobranca


@pytest.mark.parametrize('compra, cobranca', [
    (date(2026, 8, 1), date(2026, 9, 14)),
    (date(2026, 3, 1), date(2026, 4, 13)),
])
def test_vencimento_em_fim_de_semana_vai_para_segunda(compra, cobranca):
    assert card(1, 12).charge_date(compra) == cobranca
    assert cobranca.isoweekday() == 1


def test_vencimento_em_dia_util_nao_se_move():
    cobranca = card(1, 12).charge_date(date(2026, 9, 1))
    assert cobranca == date(2026, 10, 12)
    assert cobranca.isoweekday() == 1


@pytest.mark.parametrize('closing_day, due_day, compra, cobranca', [
    (5, 12, date(2026, 12, 20), date(2027, 1, 12)),
    (28, 5, date(2026, 12, 29), date(2027, 2, 5)),
    (10, 10, date(2026, 11, 15), date(2027, 1, 11)),
])
def test_virada_de_ano(closing_day, due_day, compra, cobranca):
    assert card(closing_day, due_day).charge_date(compra) == cobranca


def todas_as_compras(anos=(2024, 2026)):
    for ano in anos:
        for mes in range(1, 13):
            for dia in range(1, monthrange(ano, mes)[1] + 1):
                yield date(ano, mes, dia)


@pytest.mark.parametrize('closing_day', range(1, 32))
def test_invariantes_para_todo_vencimento_e_toda_data(closing_day):
    """As quatro propriedades que valem para qualquer configuração de cartão.

    2024 entra na varredura por ser bissexto; 2026 por ser o ano dos exemplos.
    """
    for due_day in range(1, 32):
        cartao = card(closing_day, due_day)
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
