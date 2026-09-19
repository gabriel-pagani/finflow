"""Validação, aplicação e descrição dos filtros das consultas do assistente."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.postgres.lookups import Unaccent
from django.db.models import Q, Value

from app.models import Account, Card, Category, Method, Nature, Transaction, Type


UNCATEGORIZED = 'Categoria Não Identificada'

ORIGINS = {
    'standalone': ('Avulsa', Q(installment__isnull=True, transfer__isnull=True)),
    'installment': ('Parcela de parcelamento', Q(installment__isnull=False)),
    'transfer': ('Perna de transferência', Q(transfer__isnull=False)),
}

PERIOD_DATE_FIELD = 'effective_at'
PERIOD_DATE_MEANING = 'data efetiva (no crédito, o vencimento da fatura), como nas telas'


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
    def __init__(self, user, arguments, *, default_methods=None, default_natures=None):
        self.user = user

        self.start = read_date(arguments, 'start')
        self.end = read_date(arguments, 'end')
        if self.start and self.end and self.start > self.end:
            raise QueryError(f'"start" ({self.start}) é depois de "end" ({self.end}).')

        self.accounts = read_ids(arguments, 'account', Account.objects.all())
        self.categories = read_ids(arguments, 'category', Category.objects.all())
        self.cards = read_ids(arguments, 'card', Card.objects.filter(user=user).select_related('account'))

        self.uncategorized = arguments.get('uncategorized')
        if self.uncategorized is None:
            self.uncategorized = False
        elif not isinstance(self.uncategorized, bool):
            raise QueryError(f'"uncategorized" espera true ou false. Recebido: {self.uncategorized!r}.')

        self.types = read_codes(arguments, 'type', Type.values)
        self.methods = read_codes(arguments, 'method', Method.values)
        self.natures = read_codes(arguments, 'nature', Nature.values)
        if self.methods is None and default_methods is not None:
            self.methods = list(default_methods)
        if self.natures is None and default_natures is not None:
            self.natures = list(default_natures)
        self.origins = read_codes(arguments, 'origin', tuple(ORIGINS))

        self.min_value = read_money(arguments, 'min_value')
        self.max_value = read_money(arguments, 'max_value')
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise QueryError('"min_value" é maior que "max_value": nenhuma transação caberia na faixa.')

        self.search = str(arguments.get('search') or '').strip()

    def queryset(self):
        queryset = Transaction.objects.filter(user=self.user)

        if self.start:
            queryset = queryset.filter(effective_at__gte=self.start)
        if self.end:
            queryset = queryset.filter(effective_at__lte=self.end)
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
                'date_field': PERIOD_DATE_FIELD,
                'meaning': PERIOD_DATE_MEANING,
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

        applied['not_filtered'] = 'Toda dimensão ausente deste objeto entrou inteira no recorte.'
        return applied
