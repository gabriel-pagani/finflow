from decimal import Decimal


MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def month_label(value) -> str:
    return f'{value.year} {MONTHS[value.month - 1]}'


def to_float(value: Decimal | None) -> float:
    return float(value or Decimal('0.00'))
