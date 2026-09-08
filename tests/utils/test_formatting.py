"""Formatação de valores no padrão brasileiro."""
from decimal import Decimal

import pytest

from app.utils.formatting import format_to_money


@pytest.mark.parametrize('value, esperado', [
    (Decimal('0.07'), '0,07'),
    (Decimal('10.00'), '10,00'),
    (Decimal('1234.50'), '1.234,50'),
    (Decimal('1234567.89'), '1.234.567,89'),
])
def test_troca_a_convencao_americana_pela_brasileira(value, esperado):
    assert format_to_money(value) == esperado


def test_completa_os_centavos_ausentes():
    assert format_to_money(Decimal('50')) == '50,00'
