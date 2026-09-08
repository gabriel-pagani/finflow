"""Unicidade da descrição da categoria, garantida pelo banco."""
import pytest
from django.db.utils import IntegrityError

from app.models import Category


def test_descricao_repetida_e_recusada(category):
    with pytest.raises(IntegrityError):
        Category.objects.create(description='Mercado')


def test_descricao_repetida_em_outra_caixa_e_recusada(category):
    with pytest.raises(IntegrityError):
        Category.objects.create(description='mercado')


def test_descricoes_diferentes_convivem(category):
    Category.objects.create(description='Transporte')
    assert Category.objects.count() == 2
