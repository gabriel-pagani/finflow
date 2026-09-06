"""O que todo número atravessa antes de virar série de gráfico.

Os painéis mandam o JSON pronto para o template, e o ECharts do outro lado não
lê Decimal nem date: os dois conversores ficam aqui porque quem os chama é
sempre uma view de painel, e nunca um modelo.
"""

from decimal import Decimal


MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def month_label(value):
    return f'{value.year} {MONTHS[value.month - 1]}'


def to_float(value):
    return float(value or Decimal('0.00'))
