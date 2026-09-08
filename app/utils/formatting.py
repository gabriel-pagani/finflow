from decimal import Decimal


def format_to_money(value: Decimal) -> str:
    units, _, cents = f'{value:,.2f}'.partition('.')
    return f'{units.replace(",", ".")},{cents}'
