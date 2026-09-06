"""Como cada registro chega ao assistente: código ao lado de rótulo.

O código é o que ele reenvia numa próxima chamada, o rótulo é o que ele mostra
a quem lê. Serializar é o que evita que ele traduza um no outro por conta
própria — que é onde inventa.
"""

from decimal import Decimal


def json_default(value):
    """Converte o que o json não conhece.

    Valor monetário vira string, não float: 0.1 + 0.2 em ponto flutuante não dá
    0.3, e um modelo que receba 25.999999 vai repetir esse número de volta.
    """
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    raise TypeError(f'Sem conversão para JSON: {type(value).__name__}')




def serialize_choices(choices):
    return [{'value': value, 'label': label} for value, label in choices]


def serialize_transaction(transaction):
    """Uma transação como o agente precisa vê-la, com rótulo ao lado do código.

    O código é o que se reenvia numa próxima chamada; o rótulo é o que se mostra
    a quem lê. Mandar só um dos dois obrigaria o agente a traduzir por conta
    própria, e é aí que ele inventa.
    """
    return {
        'id': transaction.id,
        'datetime': transaction.datetime,
        'account': {'id': transaction.account_id, 'description': str(transaction.account)},
        'card': {'id': transaction.card_id, 'description': str(transaction.card)} if transaction.card_id else None,
        'type': {'value': transaction.type, 'label': transaction.get_type_display()},
        'method': {'value': transaction.method, 'label': transaction.get_method_display()},
        'nature': {'value': transaction.nature, 'label': transaction.get_nature_display()},
        'category': {'id': transaction.category_id, 'description': transaction.category_display},
        'description': transaction.description,
        'value': transaction.value,
        'parcel': transaction.parcel,
        'origin': transaction.origin_display or None,
        'is_derived': transaction.is_derived,
    }


def serialize_card(card, today):
    """O cartão e o que o ciclo dele faria com uma compra feita hoje.

    As três datas calculadas evitam que o agente refaça a aritmética do ciclo —
    e erre. Elas respondem, já mastigado, "se eu lançar agora, quando vence?".
    """
    cycle = card.invoice_cycle(today)
    return {
        'id': card.id,
        'description': str(card),
        'account': {'id': card.account_id, 'description': str(card.account)},
        'last_digits': card.last_digits,
        'closing_day': card.closing_day,
        'due_day': card.due_day,
        'purchase_today': {
            'invoice_month': cycle.strftime('%Y-%m'),
            'closing_date': card.closing_date(cycle.year, cycle.month),
            'due_date': card.invoice_due_date(today),
            'explanation': (
                f'Uma compra lançada hoje ({today.isoformat()}) neste cartão entra na fatura de '
                f'{cycle.strftime("%m/%Y")} e será gravada com a data de vencimento '
                f'{card.invoice_due_date(today).isoformat()}.'
            ),
        },
    }
