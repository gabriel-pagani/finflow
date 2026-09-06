"""As duas maneiras de tratar um valor monetário, no mesmo lugar.

Uma formata para leitura e outra normaliza para cálculo. Elas já se chamaram
`money` cada uma no seu módulo, e ter o mesmo nome para trabalhos opostos é
convite a importar a errada: quem formata devolve string e não volta a somar.
"""

from decimal import Decimal

from django.utils.formats import number_format


ZERO = Decimal('0.00')


def format_money(value):
    """O valor como o usuário lê: duas casas e a vírgula do português.

    Sem o símbolo da moeda. O sistema inteiro é em real, e repetir "R$" em cada
    rótulo, linha e tooltip não acrescenta informação a quem já está olhando as
    próprias finanças.
    """
    return number_format(value, decimal_pos=2, use_l10n=True)


def to_money(value):
    """Total que veio de um Sum, já com o None do conjunto vazio resolvido."""
    return (value or ZERO).quantize(Decimal('0.01'))
