"""Faixa dos dias do ciclo, formato do final e unicidade do cartão, garantidos pelo banco."""
import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db.utils import IntegrityError

from app.models import Card


@pytest.mark.parametrize('closing_day, due_day', [(0, 12), (32, 12), (5, 0), (5, 32)])
def test_dia_fora_do_mes_e_recusado(user, account, closing_day, due_day):
    with pytest.raises(IntegrityError):
        Card.objects.create(user=user, account=account, last_digits='9999',
                            closing_day=closing_day, due_day=due_day)


@pytest.mark.parametrize('last_digits', ['', '12', '123', '12a4', ' 123'])
def test_final_que_nao_tem_quatro_numeros_e_recusado(user, account, last_digits):
    with pytest.raises(IntegrityError):
        Card.objects.create(user=user, account=account, last_digits=last_digits,
                            closing_day=5, due_day=12)


@pytest.mark.parametrize('last_digits, closing_day, due_day', [('12a4', 5, 12), ('9999', 32, 12), ('9999', 5, 32)])
def test_formulario_recusa_antes_de_chegar_ao_banco(user, account, credit_rule, last_digits, closing_day, due_day):
    cartao = Card(user=user, account=account, last_digits=last_digits,
                  closing_day=closing_day, due_day=due_day)
    with pytest.raises(ValidationError) as erro:
        cartao.full_clean()
    assert NON_FIELD_ERRORS in erro.value.error_dict


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
