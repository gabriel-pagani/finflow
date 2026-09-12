import pytest
from django.core.exceptions import ValidationError

from app.models import Card, Method


def test_cartao_sem_lancamentos_e_apagado(card):
    card.delete()
    assert not Card.objects.exists()


def test_cartao_com_transacao_nao_e_apagado(card, make_transaction):
    make_transaction(method=Method.CREDIT, card=card).save()
    with pytest.raises(ValidationError):
        card.delete()
    assert Card.objects.filter(pk=card.pk).exists()


def test_cartao_com_parcelamento_nao_e_apagado(card, make_installment):
    make_installment().save()
    with pytest.raises(ValidationError):
        card.delete()
    assert Card.objects.filter(pk=card.pk).exists()
