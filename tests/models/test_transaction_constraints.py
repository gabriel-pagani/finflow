"""As mesmas regras da transação, agora garantidas pelo banco.

Aqui o save() é chamado direto na instância, sem passar por full_clean(): é o
caminho de um script ou de um shell, que o clean() não cobre.
"""
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from app.models import Method, Nature


@pytest.mark.parametrize('value', [Decimal('0.00'), Decimal('-10.00')])
def test_valor_nao_positivo_e_recusado(make_transaction, value):
    with pytest.raises(IntegrityError):
        make_transaction(value=value).save()


def test_credito_sem_cartao_e_recusado(make_transaction):
    with pytest.raises(IntegrityError):
        make_transaction(method=Method.CREDIT).save()


def test_cartao_fora_do_credito_e_recusado(make_transaction, card):
    with pytest.raises(IntegrityError):
        make_transaction(card=card).save()


def test_categoria_fora_da_natureza_normal_e_recusada(make_transaction, category):
    with pytest.raises(IntegrityError):
        make_transaction(nature=Nature.INTERNAL, category=category).save()
