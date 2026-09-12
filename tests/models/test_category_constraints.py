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


@pytest.mark.parametrize('description', ['', '   ', ' Mercado', 'Mercado '])
def test_descricao_em_branco_ou_com_espaco_nas_pontas_e_recusada(db, description):
    with pytest.raises(IntegrityError):
        Category.objects.create(description=description)
