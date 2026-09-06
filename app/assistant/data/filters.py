"""Tudo o que o assistente pede passa por aqui antes de virar queryset.

Um parâmetro que ele escreveu errado vira FilterError com a mensagem do que se
aceita, e não um filtro silenciosamente ignorado: o modelo que não é avisado não
corrige, e apresenta ao usuário um número de um recorte que ele não pediu.
"""

import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Q

from ...models import Method, Nature, Transaction, Type
from ...utils.dates import add_months
from .aggregates import GROUPINGS
from .constants import SETTLED_METHODS
from .serializers import serialize_transaction


class FilterError(Exception):
    """Parâmetro de consulta que o assistente montou errado.

    A mensagem é escrita para o modelo ler e corrigir sozinho, e por isso diz o
    que o campo aceita. Ela nunca sobe para o usuário: quem a mostra é o loop de
    ferramentas, de volta para o modelo.
    """


# Valores que significam "sem registro do outro lado" nos filtros por id, e
# "não filtre por isto" nos filtros por código.
NULL_TOKENS = {'null', 'none', 'nenhum', 'nenhuma', 'sem'}
ALL_TOKENS = {'all', 'todos', 'todas', '*'}

DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y')

# Origem da transação, como Q, espelhando Transaction.DERIVED_FIELDS. Responde
# "quanto do meu gasto é parcelado" sem o agente ter de cruzar listas.
ORIGINS = {
    'standalone': lambda: Q(**Transaction.standalone_filters()),
    'installment': lambda: Q(installment__isnull=False),
    'transfer': lambda: Q(transfer__isnull=False),
    'investment': lambda: Q(investment__isnull=False),
}

ORDERS = {
    'recent': ('-datetime', '-id'),
    'oldest': ('datetime', 'id'),
    'largest': ('-value', '-datetime'),
    'smallest': ('value', '-datetime'),
}


def read_list(params, name):
    """Parâmetro separado por vírgula, ou None quando não veio."""
    raw = params.get(name)
    if raw is None:
        return None
    values = [part.strip() for part in raw.split(',') if part.strip()]
    return values or None


def read_codes(params, name, allowed, default):
    """Lista de códigos aceitos, com "all" desligando o filtro.

    A comparação ignora caixa, mas o que volta é a grafia canônica: os códigos de
    transação são maiúsculos (DEBIT) e os de origem, minúsculos (installment), e
    quem recebe a lista a usa como chave.

    O default só vale para o parâmetro ausente. Quem escreve method= vazio está
    dizendo "não me dê o padrão", e recebe o conjunto inteiro.
    """
    values = read_list(params, name)
    if values is None:
        return list(default) if default is not None else None
    if any(value.lower() in ALL_TOKENS for value in values):
        return None

    canonical = {str(code).upper(): str(code) for code in allowed}

    codes = []
    for value in values:
        code = canonical.get(value.upper())
        if code is None:
            raise FilterError(
                f'Valor inválido em "{name}": {value!r}. Aceitos: {", ".join(canonical.values())} (ou "all").'
            )
        codes.append(code)
    return codes


def read_ids(params, name):
    """Ids numéricos e a marca de "sem este vínculo".

    Devolve (ids, inclui_vazio). O None de ids distingue "não filtre" de "filtre
    por lista vazia", que nunca casaria com nada.
    """
    values = read_list(params, name)
    if values is None:
        return None, False

    ids, include_null = [], False
    for value in values:
        if value.lower() in NULL_TOKENS:
            include_null = True
            continue
        if not value.isdigit():
            raise FilterError(f'Valor inválido em "{name}": {value!r}. Esperado um id numérico ou "null".')
        ids.append(int(value))
    return ids, include_null


def read_date(params, name, last_day):
    """Data do recorte, aceitando dia, mês, ano e o formato brasileiro.

    `last_day` decide para onde um mês ou ano incompleto se estica: o começo da
    janela vai para o primeiro dia, o fim para o último. Sem isso, end=2026-03
    cortaria março no dia 1.
    """
    raw = (params.get(name) or '').strip()
    if not raw:
        return None

    if re.fullmatch(r'\d{4}', raw):
        year = int(raw)
        return date(year, 12, 31) if last_day else date(year, 1, 1)

    if re.fullmatch(r'\d{4}-\d{1,2}', raw):
        year, month = (int(part) for part in raw.split('-'))
        if not 1 <= month <= 12:
            raise FilterError(f'Mês inválido em "{name}": {raw!r}.')
        return date(year, month, monthrange(year, month)[1] if last_day else 1)

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    raise FilterError(
        f'Data inválida em "{name}": {raw!r}. Aceitos: "2026-03-15", "2026-03", "2026" ou "15/03/2026".'
    )


def read_int(params, name, default, minimum, maximum):
    raw = (params.get(name) or '').strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise FilterError(f'O parâmetro "{name}" espera um número inteiro. Recebido: {raw!r}.')

    value = int(raw)
    if not minimum <= value <= maximum:
        raise FilterError(f'O parâmetro "{name}" aceita de {minimum} a {maximum}. Recebido: {value}.')
    return value


def read_decimal(params, name):
    raw = (params.get(name) or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise FilterError(
            f'O parâmetro "{name}" espera um decimal com ponto. Recebido: {raw!r}.'
        )


def read_choice(params, name, allowed, default):
    raw = (params.get(name) or '').strip()
    if not raw:
        return default
    if raw.lower() not in allowed:
        raise FilterError(f'Valor inválido em "{name}": {raw!r}. Aceitos: {", ".join(allowed)}.')
    return raw.lower()


class AnalyticsFilters:
    """O recorte pedido pelo assistente, já validado e pronto para virar query.

    Guarda também de onde cada valor veio: os padrões desta API não são óbvios
    (só métodos liquidados, só natureza normal) e o agente precisa saber que eles
    entraram, ou vai narrar como "todos os gastos" um número que exclui o cartão.
    """

    DEFAULT_MONTHS = 12
    MAX_MONTHS = 120

    DEFAULT_LIMIT = 25
    MAX_LIMIT = 500
    MAX_OFFSET = 100000
    MAX_TOP = 200

    DEFAULT_GROUP_BY = ('month', 'category')
    DEFAULT_INCLUDE = ('summary', 'breakdowns', 'position')
    ALL_INCLUDES = ('summary', 'breakdowns', 'position', 'forecast', 'transactions')

    DEFAULT_FORECAST_MONTHS = 12
    MAX_FORECAST_MONTHS = 60

    def __init__(self, params, today, default_include=None, default_limit=None):
        self.today = today
        self.defaulted = []

        self.end = self.read_end(params, today)
        self.start, self.months = self.read_start(params)
        if self.start > self.end:
            raise FilterError(
                f'O início do período ({self.start.isoformat()}) é depois do fim ({self.end.isoformat()}).'
            )

        self.types = read_codes(params, 'type', Type.values, None)
        self.methods = read_codes(params, 'method', Method.values, SETTLED_METHODS)
        self.natures = read_codes(params, 'nature', Nature.values, [Nature.REGULAR])
        self.note_default(params, 'method', 'nature')

        self.account_ids, _ = read_ids(params, 'account')
        self.category_ids, self.uncategorized = read_ids(params, 'category')
        self.card_ids, self.without_card = read_ids(params, 'card')
        self.origins = read_codes(params, 'origin', tuple(ORIGINS), None)

        self.min_value = read_decimal(params, 'min_value')
        self.max_value = read_decimal(params, 'max_value')
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise FilterError('"min_value" é maior que "max_value": nenhuma transação caberia na faixa.')

        self.search = (params.get('search') or '').strip() or None

        self.group_by = self.read_group_by(params)
        self.include = self.read_include(params, default_include or self.DEFAULT_INCLUDE)
        self.note_default(params, 'group_by', 'include')

        self.top = read_int(params, 'top', 0, 0, self.MAX_TOP) or None
        # O default_limit pode ser zero, e zero é um pedido legítimo: "conte as
        # transações, não as liste". Por isso a comparação com None.
        fallback = self.DEFAULT_LIMIT if default_limit is None else default_limit
        self.limit = read_int(params, 'limit', fallback, 0, self.MAX_LIMIT)
        self.offset = read_int(params, 'offset', 0, 0, self.MAX_OFFSET)
        self.order = read_choice(params, 'order', tuple(ORDERS), 'recent')
        self.forecast_months = read_int(
            params, 'forecast_months', self.DEFAULT_FORECAST_MONTHS, 1, self.MAX_FORECAST_MONTHS
        )

    # -- leitura ----------------------------------------------------------

    def read_end(self, params, today):
        return read_date(params, 'end', last_day=True) or today

    def read_start(self, params):
        """O início da janela, e quantos meses ela cobre quando foi assim que veio.

        Recortar por dia deixaria o mês mais antigo pela metade e a série
        começaria com um degrau falso; por isso a janela por meses sempre abre no
        primeiro dia do mês.
        """
        start = read_date(params, 'start', last_day=False)
        if start is not None:
            return start, None

        months = read_int(params, 'months', self.DEFAULT_MONTHS, 1, self.MAX_MONTHS)
        if 'months' not in params:
            self.defaulted.append('months')
        return add_months(self.end.replace(day=1), -(months - 1)), months

    def read_group_by(self, params):
        values = read_list(params, 'group_by') or list(self.DEFAULT_GROUP_BY)
        for value in values:
            if value.lower() not in GROUPINGS:
                raise FilterError(
                    f'Eixo desconhecido em "group_by": {value!r}. Aceitos: {", ".join(GROUPINGS)}.'
                )
        # dict.fromkeys em vez de set: a ordem pedida é a ordem devolvida, e
        # repetir um eixo não deve repetir a quebra.
        return list(dict.fromkeys(value.lower() for value in values))

    def read_include(self, params, default):
        values = read_list(params, 'include')
        if values is None:
            return list(default)
        if any(value.lower() in ALL_TOKENS for value in values):
            return list(self.ALL_INCLUDES)

        for value in values:
            if value.lower() not in self.ALL_INCLUDES:
                raise FilterError(
                    f'Seção desconhecida em "include": {value!r}. Aceitas: {", ".join(self.ALL_INCLUDES)}.'
                )
        return list(dict.fromkeys(value.lower() for value in values))

    def note_default(self, params, *names):
        """Registra os filtros que o cliente não mandou e receberam padrão."""
        self.defaulted.extend(name for name in names if name not in params)

    # -- consulta ---------------------------------------------------------

    def dimensional_q(self):
        """Os filtros que dizem "de qual pedaço do cadastro", sem período nem medida.

        Separados dos demais porque a previsão os aceita — faz sentido prever o
        crédito de uma conta ou categoria — mas não aceita o recorte de período,
        de tipo, de método nem de natureza, que ela define por conta própria.
        """
        query = Q()

        if self.account_ids is not None:
            query &= Q(account_id__in=self.account_ids)

        if self.category_ids is not None:
            categories = Q(category_id__in=self.category_ids)
            if self.uncategorized:
                categories |= Q(category__isnull=True)
            query &= categories

        if self.card_ids is not None:
            cards = Q(card_id__in=self.card_ids)
            if self.without_card:
                cards |= Q(card__isnull=True)
            query &= cards

        if self.min_value is not None:
            query &= Q(value__gte=self.min_value)
        if self.max_value is not None:
            query &= Q(value__lte=self.max_value)

        if self.search:
            query &= Q(description__icontains=self.search)

        if self.origins is not None:
            origins = Q()
            for name in self.origins:
                origins |= ORIGINS[name]()
            query &= origins

        return query

    def queryset(self, user):
        """As transações do recorte inteiro: período, medidas e dimensões."""
        queryset = Transaction.objects.filter(user=user).filter(
            datetime__date__gte=self.start,
            datetime__date__lte=self.end,
        )

        if self.types is not None:
            queryset = queryset.filter(type__in=self.types)
        if self.methods is not None:
            queryset = queryset.filter(method__in=self.methods)
        if self.natures is not None:
            queryset = queryset.filter(nature__in=self.natures)

        return queryset.filter(self.dimensional_q())

    def transactions(self, queryset):
        """A fatia listada da consulta, na ordem pedida."""
        if not self.limit:
            return []
        page = queryset.order_by(*ORDERS[self.order])[self.offset:self.offset + self.limit]
        return [
            serialize_transaction(transaction)
            for transaction in page.select_related('account', 'card__account', 'category')
        ]

    # -- eco --------------------------------------------------------------

    def describe(self):
        """O recorte que realmente valeu, para o agente conferir antes de afirmar.

        Período, medidas e eixos aparecem sempre, mesmo vindos de padrão: são
        eles que mudam o sentido de um total. Os filtros de dimensão só aparecem
        quando foram pedidos, senão a resposta gastaria tokens repetindo nulos.
        """
        applied = {
            'period': {'start': self.start, 'end': self.end},
            'type': self.types or 'todos',
            'method': self.methods or 'todos',
            'nature': self.natures or 'todas',
            'group_by': self.group_by,
            'include': self.include,
        }
        if self.months is not None:
            applied['period']['months'] = self.months
        if self.defaulted:
            applied['defaulted'] = sorted(set(self.defaulted))

        optional = {
            'account': self.account_ids,
            'category': self.category_ids,
            'card': self.card_ids,
            'origin': self.origins,
            'min_value': self.min_value,
            'max_value': self.max_value,
            'search': self.search,
        }
        applied.update({name: value for name, value in optional.items() if value})

        if self.uncategorized:
            applied['category_includes_null'] = True
        if self.without_card:
            applied['card_includes_null'] = True
        if 'transactions' in self.include:
            applied['transactions'] = {'limit': self.limit, 'offset': self.offset, 'order': self.order}

        return applied
