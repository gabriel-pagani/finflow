"""Consultas e agregações expostas às ferramentas do assistente."""

from decimal import Decimal

from django.db.models import Case, CharField, Count, Sum, Value, When
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek, TruncYear

from app.models import Account, BusinessRule, Card, Category, Method, Nature, Transaction, Type
from app.scopes import ANALYTIC_NATURES, OVERVIEW_METHODS

from .query_filters import (
    Filters, ORIGINS, PERIOD_DATE_FIELD, PERIOD_DATE_MEANING, QueryError,
    UNCATEGORIZED, labeled, money, read_choice, read_codes, read_date,
    read_ids, read_int, read_money,
)


CREDIT_OVERLAP_WARNING = {
    'code': 'credit_with_account_outflow',
    'message': (
        'O recorte mistura Crédito com Débito ou Não Se Aplica. A compra no cartão e o pagamento da fatura '
        'podem representar o mesmo gasto, duplicando as saídas e distorcendo o saldo.'
    ),
}

ORDERS = {
    'recent': ('-effective_at', '-id'),
    'oldest': ('effective_at', 'id'),
    'largest': ('-value', '-effective_at'),
    'smallest': ('value', '-effective_at'),
}

# Os tetos de linha por retorno moram abaixo do MAX_TOOL_OUTPUT do client: uma
# página cheia precisa caber na conversa, senão a consulta roda no banco e é
# descartada na volta, gastando uma rodada à toa. Para ver mais, o modelo pagina
# com offset; para somar, usa analisar_transacoes.
MAX_GROUPS = 120
MAX_AXES = 2
DEFAULT_LIMIT = 50
MAX_LIMIT = 60


def temporal_axis(truncate, key_format, label_format):
    def build():
        return {
            'annotate': {'_bucket': truncate(PERIOD_DATE_FIELD)},
            'fields': ('_bucket',),
            'key': lambda row: {'code': row['_bucket'].strftime(key_format), 'label': row['_bucket'].strftime(label_format)},
            'temporal': True,
        }
    return build


def field_axis(fields, key):
    def build():
        return {'annotate': {}, 'fields': fields, 'key': key, 'temporal': False}
    return build


AXES = {
    'year': temporal_axis(TruncYear, '%Y', '%Y'),
    'month': temporal_axis(TruncMonth, '%Y-%m', '%m/%Y'),
    'week': temporal_axis(TruncWeek, '%Y-%m-%d', 'semana de %d/%m/%Y'),
    'day': temporal_axis(TruncDay, '%Y-%m-%d', '%d/%m/%Y'),
    'account': field_axis(('account_id', 'account__description'), lambda row: {'id': row['account_id'], 'label': row['account__description']}),
    'category': field_axis(('category_id', 'category__description'), lambda row: {'id': row['category_id'], 'label': row['category__description'] or UNCATEGORIZED}),
    'card': field_axis(
        ('card_id', 'card__account__description', 'card__last_digits'),
        lambda row: {'id': row['card_id'], 'label': f'{row["card__account__description"]} (final {row["card__last_digits"]})' if row['card_id'] else 'Sem cartão'},
    ),
    'type': field_axis(('type',), lambda row: labeled(row['type'], Type)),
    'method': field_axis(('method',), lambda row: labeled(row['method'], Method)),
    'nature': field_axis(('nature',), lambda row: labeled(row['nature'], Nature)),
    'origin': lambda: {
        'annotate': {'_origin': Case(
            When(installment__isnull=False, then=Value('installment')),
            When(transfer__isnull=False, then=Value('transfer')),
            default=Value('standalone'),
            output_field=CharField(),
        )},
        'fields': ('_origin',),
        'key': lambda row: {'code': row['_origin'], 'label': ORIGINS[row['_origin']][0]},
        'temporal': False,
    },
}


def totals(income, outcome, count):
    return {'income': money(income), 'outcome': money(outcome), 'net': money(income - outcome), 'count': count}


def summarize(queryset):
    rows = {row['type']: row for row in queryset.order_by().values('type').annotate(total=Sum('value'), count=Count('id'))}
    income = rows.get(Type.IN, {}).get('total') or Decimal('0')
    outcome = rows.get(Type.OUT, {}).get('total') or Decimal('0')
    return totals(income, outcome, sum(row['count'] for row in rows.values()))


def read_axes(arguments):
    raw = arguments.get('group_by')
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_AXES or any(axis not in AXES for axis in raw) or len(set(raw)) != len(raw):
        raise QueryError(f'"group_by" espera até {MAX_AXES} eixos distintos entre {", ".join(AXES)}. Recebido: {raw!r}.')
    return raw


def group(queryset, names):
    axes = [AXES[name]() for name in names]

    annotations, fields = {}, []
    for axis in axes:
        annotations.update(axis['annotate'])
        fields.extend(axis['fields'])

    rows = queryset.order_by().annotate(**annotations).values(*fields, 'type').annotate(total=Sum('value'), count=Count('id'))

    grouped = {}
    for row in rows:
        keys = [axis['key'](row) for axis in axes]
        identity = tuple(str(key.get('code', key.get('id'))) for key in keys)
        entry = grouped.setdefault(identity, {'keys': keys, 'income': Decimal('0'), 'outcome': Decimal('0'), 'count': 0})
        entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']
        entry['count'] += row['count']

    def order(entry):
        temporal = tuple(entry['keys'][index]['code'] for index, axis in enumerate(axes) if axis['temporal'])
        return temporal, -(entry['income'] + entry['outcome'])

    entries = sorted(grouped.values(), key=order)

    return [
        {'keys': dict(zip(names, entry['keys'])), **totals(entry['income'], entry['outcome'], entry['count'])}
        for entry in entries
    ]


def analyze_transactions(user, arguments):
    filters = Filters(user, arguments, default_methods=OVERVIEW_METHODS, default_natures=ANALYTIC_NATURES)
    names = read_axes(arguments)
    queryset = filters.queryset()

    payload = {'filters': filters.describe(), 'total': summarize(queryset)}

    includes_outcome = filters.types is None or Type.OUT in filters.types
    mixes_credit_and_account_outflow = (
        filters.methods
        and Method.CREDIT in filters.methods
        and any(method in filters.methods for method in OVERVIEW_METHODS)
    )
    if includes_outcome and mixes_credit_and_account_outflow:
        payload['warnings'] = [CREDIT_OVERLAP_WARNING]

    if names:
        groups = group(queryset, names)
        payload['group_by'] = names
        payload['groups'] = groups[:MAX_GROUPS]
        if len(groups) > MAX_GROUPS:
            payload['truncated'] = f'Só as primeiras {MAX_GROUPS} de {len(groups)} linhas vieram. O total acima continua sendo o do recorte inteiro; estreite os filtros para ver o resto.'

    return payload


def serialize_transaction(transaction):
    origin = None
    if transaction.installment_id:
        origin = {'kind': 'installment', 'installment_id': transaction.installment_id, 'parcel': transaction.parcel, 'parcels': transaction.installment.installments}
    elif transaction.transfer_id:
        origin = {'kind': 'transfer', 'transfer_id': transaction.transfer_id}

    return {
        'id': transaction.pk,
        'occurred_at': transaction.occurred_at.isoformat(),
        'effective_at': transaction.effective_at.isoformat(),
        'account': {'id': transaction.account_id, 'label': str(transaction.account)},
        'card': {'id': transaction.card_id, 'label': str(transaction.card)} if transaction.card_id else None,
        'type': labeled(transaction.type, Type),
        'method': labeled(transaction.method, Method),
        'nature': labeled(transaction.nature, Nature),
        'category': {'id': transaction.category_id, 'label': transaction.category_display},
        'description': transaction.description,
        'value': money(transaction.value),
        'origin': origin,
    }


def list_transactions(user, arguments):
    filters = Filters(user, arguments)
    order = read_choice(arguments, 'order', tuple(ORDERS), 'recent')
    limit = read_int(arguments, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = read_int(arguments, 'offset', 0, 0, 10 ** 6)

    queryset = filters.queryset()
    count = queryset.count()
    page = queryset.select_related('account', 'card__account', 'category', 'installment').order_by(*ORDERS[order])[offset:offset + limit]
    transactions = [serialize_transaction(transaction) for transaction in page]

    payload = {
        'filters': filters.describe(),
        'count': count,
        'offset': offset,
        'order': order,
        'transactions': transactions,
    }
    if offset + len(transactions) < count:
        payload['has_more'] = f'Vieram {len(transactions)} de {count}. Para somar, use analisar_transacoes em vez de somar a lista.'
    return payload


def balance(user, arguments):
    accounts = read_ids(arguments, 'account', Account.objects.all())
    until = read_date(arguments, 'until')

    queryset = Transaction.objects.filter(user=user, method__in=[Method.DEBIT, Method.NOT_APPLICABLE])
    if accounts is not None:
        queryset = queryset.filter(account_id__in=accounts)
    if until:
        queryset = queryset.filter(effective_at__lte=until)

    rows = queryset.order_by().values('account_id', 'account__description', 'type').annotate(total=Sum('value'), count=Count('id'))

    by_account = {}
    for row in rows:
        entry = by_account.setdefault(row['account_id'], {'label': row['account__description'], 'income': Decimal('0'), 'outcome': Decimal('0'), 'count': 0})
        entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']
        entry['count'] += row['count']

    income = sum((entry['income'] for entry in by_account.values()), Decimal('0'))
    outcome = sum((entry['outcome'] for entry in by_account.values()), Decimal('0'))

    return {
        'scope': (
            'Saldo acumulado, igual ao card de Saldo da Visão Geral: todas as naturezas, só Débito e Não Se Aplica. '
            'O crédito fica fora porque ainda vai ser pago.'
        ),
        'until': until.isoformat() if until else 'sem limite de data',
        'accounts': [
            {'id': pk, 'label': entry['label'], **totals(entry['income'], entry['outcome'], entry['count'])}
            for pk, entry in sorted(by_account.items(), key=lambda item: item[1]['label'])
        ],
        'total': totals(income, outcome, sum(entry['count'] for entry in by_account.values())),
    }


def registry(user, today):
    allowed = {}
    for rule in BusinessRule.objects.all():
        allowed.setdefault(rule.account_id, []).append({'type': labeled(rule.type, Type), 'method': labeled(rule.method, Method)})

    return {
        'accounts': [
            {'id': account.pk, 'label': str(account), 'allowed_combinations': allowed.get(account.pk, [])}
            for account in Account.objects.all()
        ],
        'categories': [{'id': category.pk, 'label': str(category)} for category in Category.objects.all()],
        'cards': [
            {
                'id': card.pk,
                'label': str(card),
                'account_id': card.account_id,
                'last_digits': card.last_digits,
                'closing_day': card.closing_day,
                'due_day': card.due_day,
                'purchase_today_charged_at': card.charge_date(today).isoformat(),
            }
            for card in Card.objects.filter(user=user).select_related('account')
        ],
        'types': [labeled(code, Type) for code in Type.values],
        'methods': [labeled(code, Method) for code in Method.values],
        'natures': [labeled(code, Nature) for code in Nature.values],
    }
