"""Preenchimento da data efetiva no save() da transação."""
from datetime import date

from app.models import Method


def test_sem_cartao_a_data_efetiva_e_a_da_transacao(make_transaction):
    transacao = make_transaction(occurred_at=date(2026, 9, 4))
    transacao.save()
    assert transacao.effective_at == date(2026, 9, 4)


def test_no_credito_a_data_efetiva_segue_o_ciclo_do_cartao(make_transaction, card):
    transacao = make_transaction(method=Method.CREDIT, card=card, occurred_at=date(2026, 9, 4))
    transacao.save()
    assert transacao.effective_at == date(2026, 9, 14)


def test_data_efetiva_e_recalculada_ao_editar_a_transacao(make_transaction, card):
    transacao = make_transaction(method=Method.CREDIT, card=card, occurred_at=date(2026, 9, 4))
    transacao.save()
    transacao.occurred_at = date(2026, 9, 11)
    transacao.save()
    assert transacao.effective_at == date(2026, 10, 12)


def test_data_efetiva_e_persistida(make_transaction, card):
    transacao = make_transaction(method=Method.CREDIT, card=card, occurred_at=date(2026, 9, 4))
    transacao.save()
    transacao.refresh_from_db()
    assert transacao.effective_at == date(2026, 9, 14)
