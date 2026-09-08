"""Faixa dos dias do ciclo e unicidade do cartão, garantidas pelo banco."""
import pytest
from django.db.utils import IntegrityError

from app.models import Card


@pytest.mark.parametrize('closing_day, due_day', [(0, 12), (32, 12), (5, 0), (5, 32)])
def test_dia_fora_do_mes_e_recusado(user, account, closing_day, due_day):
    with pytest.raises(IntegrityError):
        Card.objects.create(user=user, account=account, last_digits='9999',
                            closing_day=closing_day, due_day=due_day)


def test_mesmo_final_na_mesma_conta_e_usuario_e_recusado(card, user, account):
    with pytest.raises(IntegrityError):
        Card.objects.create(user=user, account=account, last_digits='1234',
                            closing_day=10, due_day=20)


def test_mesmo_final_para_outro_usuario_e_aceito(card, other_user, account):
    Card.objects.create(user=other_user, account=account, last_digits='1234',
                        closing_day=10, due_day=20)
    assert Card.objects.count() == 2


def test_mesmo_final_em_outra_conta_e_aceito(card, user, other_account):
    Card.objects.create(user=user, account=other_account, last_digits='1234',
                        closing_day=10, due_day=20)
    assert Card.objects.count() == 2
