"""
Onde cada campo cai no modal. O grid tem duas colunas e rende os campos na
ordem em que o formulário os entrega, então a ordem é a própria disposição.
"""
from app.forms import TransferForm


def test_transferencia_junta_as_contas_e_poe_a_data_ao_lado_do_valor(user):
    assert list(TransferForm(user=user).fields) == ['origin', 'destination', 'description', 'value', 'occurred_at']
