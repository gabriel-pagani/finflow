from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.postgres.lookups import Unaccent
from django.db.models import Case, CharField, Count, Q, Sum, Value, When
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek, TruncYear

from app.models import Account, BusinessRule, Card, Category, Method, Nature, Transaction, Type


UNCATEGORIZED = 'Categoria Não Identificada'

ORIGINS = {
    'standalone': ('Avulsa', Q(installment__isnull=True, transfer__isnull=True)),
    'installment': ('Parcela de parcelamento', Q(installment__isnull=False)),
    'transfer': ('Perna de transferência', Q(transfer__isnull=False)),
}

DATE_FIELDS = {
    'effective_at': 'data efetiva (no crédito, o vencimento da fatura)',
    'occurred_at': 'data da transação (no crédito, o dia da compra)',
}

ORDERS = {
    'recent': ('-effective_at', '-id'),
    'oldest': ('effective_at', 'id'),
    'largest': ('-value', '-effective_at'),
    'smallest': ('value', '-effective_at'),
}

MAX_GROUPS = 300
MAX_AXES = 2
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class QueryError(Exception):
    pass


def money(value):
    return f'{(value or Decimal("0")):.2f}'


def labeled(code, choices):
    return {'code': code, 'label': choices(code).label}


def read_date(arguments, name):
    raw = arguments.get(name)
    if raw in (None, ''):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise QueryError(f'"{name}" espera uma data no formato AAAA-MM-DD. Recebido: {raw!r}.')


def read_ids(arguments, name, queryset):
    raw = arguments.get(name)
    if raw in (None, []):
        return None
    if not isinstance(raw, list) or not all(isinstance(value, int) and not isinstance(value, bool) for value in raw):
        raise QueryError(f'"{name}" espera uma lista de ids numéricos. Recebido: {raw!r}.')

    found = {record.pk: str(record) for record in queryset.filter(pk__in=raw)}
    missing = sorted(set(raw) - set(found))
    if missing:
        raise QueryError(f'Nenhum registro de "{name}" com os ids {missing}. Os ids válidos estão em consultar_cadastros.')
    return found


def read_codes(arguments, name, accepted):
    raw = arguments.get(name)
    if raw in (None, []):
        return None
    if not isinstance(raw, list) or any(value not in accepted for value in raw):
        raise QueryError(f'"{name}" espera uma lista com valores entre {", ".join(accepted)}. Recebido: {raw!r}.')
    return list(dict.fromkeys(raw))


def read_money(arguments, name):
    raw = arguments.get(name)
    if raw in (None, ''):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise QueryError(f'"{name}" espera um valor decimal com ponto, como "25.90". Recebido: {raw!r}.')
    if value < 0:
        raise QueryError(f'"{name}" não pode ser negativo: o que separa entrada de saída é o tipo.')
    return value


def read_int(arguments, name, default, minimum, maximum):
    raw = arguments.get(name)
    if raw is None:
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or not minimum <= raw <= maximum:
        raise QueryError(f'"{name}" espera um inteiro de {minimum} a {maximum}. Recebido: {raw!r}.')
    return raw


def read_choice(arguments, name, accepted, default):
    raw = arguments.get(name)
    if raw in (None, ''):
        return default
    if raw not in accepted:
        raise QueryError(f'"{name}" aceita {", ".join(accepted)}. Recebido: {raw!r}.')
    return raw


class Filters:
    def __init__(self, user, arguments):
        self.user = user

        self.date_field = read_choice(arguments, 'date_field', tuple(DATE_FIELDS), 'effective_at')
        self.start = read_date(arguments, 'start')
        self.end = read_date(arguments, 'end')
        if self.start and self.end and self.start > self.end:
            raise QueryError(f'"start" ({self.start}) é depois de "end" ({self.end}).')

        self.accounts = read_ids(arguments, 'account', Account.objects.all())
        self.categories = read_ids(arguments, 'category', Category.objects.all())
        self.cards = read_ids(arguments, 'card', Card.objects.filter(user=user).select_related('account'))

        self.uncategorized = arguments.get('uncategorized', False)
        if not isinstance(self.uncategorized, bool):
            raise QueryError(f'"uncategorized" espera true ou false. Recebido: {self.uncategorized!r}.')

        self.types = read_codes(arguments, 'type', Type.values)
        self.methods = read_codes(arguments, 'method', Method.values)
        self.natures = read_codes(arguments, 'nature', Nature.values)
        self.origins = read_codes(arguments, 'origin', tuple(ORIGINS))

        self.min_value = read_money(arguments, 'min_value')
        self.max_value = read_money(arguments, 'max_value')
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise QueryError('"min_value" é maior que "max_value": nenhuma transação caberia na faixa.')

        self.search = str(arguments.get('search') or '').strip()

    def queryset(self):
        queryset = Transaction.objects.filter(user=self.user)

        if self.start:
            queryset = queryset.filter(**{f'{self.date_field}__gte': self.start})
        if self.end:
            queryset = queryset.filter(**{f'{self.date_field}__lte': self.end})
        if self.accounts is not None:
            queryset = queryset.filter(account_id__in=self.accounts)
        if self.cards is not None:
            queryset = queryset.filter(card_id__in=self.cards)

        if self.categories is not None or self.uncategorized:
            condition = Q(category_id__in=self.categories or [])
            if self.uncategorized:
                condition |= Q(category__isnull=True)
            queryset = queryset.filter(condition)

        if self.types:
            queryset = queryset.filter(type__in=self.types)
        if self.methods:
            queryset = queryset.filter(method__in=self.methods)
        if self.natures:
            queryset = queryset.filter(nature__in=self.natures)
        if self.origins:
            condition = Q()
            for origin in self.origins:
                condition |= ORIGINS[origin][1]
            queryset = queryset.filter(condition)

        if self.min_value is not None:
            queryset = queryset.filter(value__gte=self.min_value)
        if self.max_value is not None:
            queryset = queryset.filter(value__lte=self.max_value)
        if self.search:
            queryset = queryset.annotate(
                description_unaccent=Unaccent('description'),
            ).filter(description_unaccent__icontains=Unaccent(Value(self.search)))

        return queryset

    # Devolve o recorte que de fato valeu, com rótulos, para o modelo conferir
    # antes de narrar. O que não aparece aqui não foi filtrado.
    def describe(self):
        applied = {
            'period': {
                'date_field': self.date_field,
                'meaning': DATE_FIELDS[self.date_field],
                'start': self.start.isoformat() if self.start else 'sem limite',
                'end': self.end.isoformat() if self.end else 'sem limite',
            },
        }

        for name, found in (('account', self.accounts), ('category', self.categories), ('card', self.cards)):
            if found is not None:
                applied[name] = [{'id': pk, 'label': label} for pk, label in found.items()]
        if self.uncategorized:
            applied['uncategorized'] = True

        for name, codes, choices in (('type', self.types, Type), ('method', self.methods, Method), ('nature', self.natures, Nature)):
            if codes:
                applied[name] = [labeled(code, choices) for code in codes]
        if self.origins:
            applied['origin'] = [{'code': origin, 'label': ORIGINS[origin][0]} for origin in self.origins]

        if self.min_value is not None:
            applied['min_value'] = money(self.min_value)
        if self.max_value is not None:
            applied['max_value'] = money(self.max_value)
        if self.search:
            applied['search'] = self.search

        applied['not_filtered'] = 'Toda dimensão ausente deste objeto entrou inteira no recorte, inclusive todas as naturezas e métodos.'
        return applied


def temporal_axis(truncate, key_format, label_format):
    def build(date_field):
        return {
            'annotate': {'_bucket': truncate(date_field)},
            'fields': ('_bucket',),
            'key': lambda row: {'code': row['_bucket'].strftime(key_format), 'label': row['_bucket'].strftime(label_format)},
            'temporal': True,
        }
    return build


def field_axis(fields, key):
    def build(date_field):
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
    'origin': lambda date_field: {
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


def group(queryset, names, date_field):
    axes = [AXES[name](date_field) for name in names]

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
    filters = Filters(user, arguments)
    names = read_axes(arguments)
    queryset = filters.queryset()

    payload = {'filters': filters.describe(), 'total': summarize(queryset)}

    if names:
        groups = group(queryset, names, filters.date_field)
        payload['group_by'] = names
        payload['groups'] = groups[:MAX_GROUPS]
        if len(groups) > MAX_GROUPS:
            payload['truncated'] = f'Só as primeiras {MAX_GROUPS} de {len(groups)} linhas vieram. O total acima continua sendo o do recorte inteiro; estreite os filtros para ver o resto.'

    return payload


def serialize_transaction(transaction):
    origin = None
    if transaction.installment_id:
        origin = {'kind': 'installment', 'id': transaction.installment_id, 'parcel': transaction.parcel, 'parcels': transaction.installment.installments}
    elif transaction.transfer_id:
        origin = {'kind': 'transfer', 'id': transaction.transfer_id}

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
