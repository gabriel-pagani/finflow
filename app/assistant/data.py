"""Consulta financeira: o que o assistente lê antes de responder ou registrar.

Este módulo é a metade de leitura do antigo app/api.py, que servia o agente do
n8n por HTTP. O agente agora roda dentro do processo, então o que era rota virou
chamada de função — mas o recorte continua sendo o mesmo, e por um motivo que não
mudou com a saída do n8n: o custo de um agente se mede em tokens por mensagem, e
mandar o extrato inteiro a cada pergunta é o que faz ele somar errado.

Toda função aqui recebe `user` como primeiro argumento e filtra por ele. Isso não
é uma regra que o modelo precise obedecer — é o argumento da consulta. Não existe
caminho, nem prompt, que faça `analytics(user, ...)` devolver o dinheiro de outra
pessoa: a query nunca é montada sem o dono.

A análise é filtrável por período, conta, categoria, cartão, método, tipo,
natureza, origem, valor e descrição, e quem chama escolhe também por quais eixos
quer os totais quebrados. Sem isso o assistente só saberia perguntar "os últimos
N meses" e teria de somar o resto na mão — que é onde ele erra, e caro.
"""

import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Count, DecimalField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek, TruncYear

from ..models import (
    Account,
    BusinessRule,
    Card,
    Category,
    Contribution,
    Investment,
    Method,
    Nature,
    Redemption,
    Transaction,
    Type,
    Yield,
    add_months,
)


ZERO = Decimal('0.00')

# Métodos que o painel do realizado considera: dinheiro que já saiu da conta. O
# crédito fica de fora porque ainda vai vencer, e somá-lo ao saldo contaria duas
# vezes a mesma compra — uma agora, outra quando a fatura for paga. É o padrão da
# análise, não uma amarra: quem passar method= escolhe outro recorte.
SETTLED_METHODS = [Method.DEBIT, Method.NOT_APPLICABLE]

# Rótulos do que não tem registro do outro lado. Ficam aqui, e não espalhados
# pelas funções de agregação, porque o assistente compara a string que recebe.
UNCATEGORIZED = 'Categoria Não Identificada'
NO_CARD = 'Sem Cartão'


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


def money(value):
    """Total que veio de um Sum, já com o None do conjunto vazio resolvido."""
    return (value or ZERO).quantize(Decimal('0.01'))




# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------

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


def investment_total(model):
    """Soma de um tipo de lançamento por investimento, como subconsulta.

    Somar aplicações, resgates e rendimentos numa annotate só produziria o
    produto cartesiano das três junções: cada aplicação apareceria uma vez por
    resgate, e os totais sairiam multiplicados. Uma subconsulta por relação
    mantém cada soma isolada — e continua sendo uma query só, ao contrário de
    percorrer os investimentos em Python.
    """
    return Coalesce(
        Subquery(
            model.objects.filter(investment=OuterRef('pk'))
            .values('investment')
            .annotate(total=Sum('value'))
            .values('total')[:1],
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        ZERO,
    )


# --------------------------------------------------------------------------
# Filtros da análise
#
# Tudo o que o assistente pede passa por aqui antes de virar queryset. Um
# parâmetro que ele escreveu errado vira FilterError com a mensagem do que se
# aceita, e não um filtro silenciosamente ignorado: o modelo que não é avisado
# não corrige, e apresenta ao usuário um número de um recorte que ele não pediu.
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Agregação
#
# Todo eixo devolve linhas da mesma forma — {key, label, income, outcome, net,
# count} —, e é isso que deixa o agente aprender a ler uma quebra e saber ler as
# outras. Quebrar por mês e quebrar por categoria são recortes do mesmo conjunto:
# as linhas de cada eixo somam sempre o mesmo total.
# --------------------------------------------------------------------------

def month_grouping(truncate, key_format, label_format):
    return {
        'annotate': {'bucket': truncate('datetime')},
        'fields': ('bucket',),
        'key': lambda row: row['bucket'].strftime(key_format),
        'label': lambda row: row['bucket'].strftime(label_format),
        'chronological': True,
    }


GROUPINGS = {
    'month': month_grouping(TruncMonth, '%Y-%m', '%m/%Y'),
    'day': month_grouping(TruncDay, '%Y-%m-%d', '%d/%m/%Y'),
    'week': month_grouping(TruncWeek, '%Y-%m-%d', 'Semana de %d/%m/%Y'),
    'year': month_grouping(TruncYear, '%Y', '%Y'),
    'category': {
        'fields': ('category_id', 'category__description'),
        'key': lambda row: row['category_id'],
        'label': lambda row: row['category__description'] or UNCATEGORIZED,
    },
    'account': {
        'fields': ('account_id', 'account__description'),
        'key': lambda row: row['account_id'],
        'label': lambda row: row['account__description'],
    },
    'card': {
        'fields': ('card_id', 'card__last_digits', 'card__account__description'),
        'key': lambda row: row['card_id'],
        'label': lambda row: (
            f'{row["card__account__description"]} (final {row["card__last_digits"]})'
            if row['card_id'] else NO_CARD
        ),
    },
    'method': {
        'fields': ('method',),
        'key': lambda row: row['method'],
        'label': lambda row: Method(row['method']).label,
    },
    'type': {
        'fields': ('type',),
        'key': lambda row: row['type'],
        'label': lambda row: Type(row['type']).label,
    },
    'nature': {
        'fields': ('nature',),
        'key': lambda row: row['nature'],
        'label': lambda row: Nature(row['nature']).label,
    },
}


def serialize_row(entry):
    return {
        'key': entry['key'],
        'label': entry['label'],
        'income': money(entry['income']),
        'outcome': money(entry['outcome']),
        'net': money(entry['income'] - entry['outcome']),
        'count': entry['count'],
    }


def collapse(entries, top):
    """Corta a cauda da quebra numa linha "outros", em vez de descartá-la.

    Descartar economizaria os mesmos tokens, mas as linhas deixariam de somar o
    total do recorte — e um agente que confere a soma e não fecha ou desiste da
    resposta ou inventa a diferença.
    """
    head, tail = entries[:top], entries[top:]
    others = {
        'key': 'others',
        'label': f'Outros ({len(tail)})',
        'income': sum((entry['income'] for entry in tail), ZERO),
        'outcome': sum((entry['outcome'] for entry in tail), ZERO),
        'count': sum(entry['count'] for entry in tail),
    }
    return head + [others]


def breakdown(queryset, name, top=None):
    """Totais do recorte quebrados por um eixo.

    O order_by() vazio é obrigatório: a ordenação padrão do modelo entraria no
    GROUP BY e a agregação sairia repartida por datetime, uma linha por
    transação. Só a ordenação por agregado, aplicada depois em Python, é segura.
    """
    spec = GROUPINGS[name]

    rows = queryset.order_by()
    if 'annotate' in spec:
        rows = rows.annotate(**spec['annotate'])
    rows = rows.values(*spec['fields'], 'type').annotate(total=Sum('value'), count=Count('id'))

    grouped = {}
    for row in rows:
        key = spec['key'](row)
        entry = grouped.setdefault(
            key, {'key': key, 'label': spec['label'](row), 'income': ZERO, 'outcome': ZERO, 'count': 0}
        )
        entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']
        entry['count'] += row['count']

    entries = list(grouped.values())
    if spec.get('chronological'):
        entries.sort(key=lambda entry: entry['key'])
    else:
        # Movimento total, e não só saída: numa consulta filtrada por entradas a
        # ordenação por saída deixaria todas as linhas empatadas em zero.
        entries.sort(key=lambda entry: entry['income'] + entry['outcome'], reverse=True)
        if top and len(entries) > top:
            entries = collapse(entries, top)

    return [serialize_row(entry) for entry in entries]


def summarize(queryset):
    """Os totais do recorte inteiro, sem quebra."""
    rows = {
        row['type']: row
        for row in queryset.order_by().values('type').annotate(total=Sum('value'), count=Count('id'))
    }

    income = money(rows.get(Type.IN, {}).get('total'))
    outcome = money(rows.get(Type.OUT, {}).get('total'))

    return {
        'income': income,
        'outcome': outcome,
        'net': money(income - outcome),
        'transactions': sum(row['count'] for row in rows.values()),
    }


# --------------------------------------------------------------------------
# Seções da consulta
# --------------------------------------------------------------------------

def options(user, today):
    """Tudo que pode ser escolhido num lançamento, com o que cada conta aceita.

    Conta e categoria são cadastros globais, sem dono: todo usuário enxerga os
    mesmos. Cartão tem dono, e por isso é filtrado.
    """
    allowed = {}
    for rule in BusinessRule.objects.all():
        allowed.setdefault(rule.account_id, []).append({
            'type': rule.type,
            'method': rule.method,
            'label': f'{Type(rule.type).label} em {Method(rule.method).label}',
        })

    cards = Card.objects.filter(user=user).select_related('account')
    cards_by_account = {}
    for card in cards:
        cards_by_account.setdefault(card.account_id, []).append(card.id)

    return {
        'accounts': [
            {
                'id': account.id,
                'description': account.description,
                'allowed_combinations': allowed.get(account.id, []),
                'card_ids': cards_by_account.get(account.id, []),
            }
            for account in Account.objects.all()
        ],
        'categories': [
            {'id': category.id, 'description': category.description}
            for category in Category.objects.all()
        ],
        'cards': [serialize_card(card, today) for card in cards],
        'types': serialize_choices(Type.choices),
        'methods': serialize_choices(Method.choices),
        'natures': serialize_choices(Nature.choices),
    }


POSITION_SCOPE = (
    'Posição acumulada: todas as naturezas, sem recorte de período e sem os filtros da consulta. '
    'Só métodos liquidados — o crédito ainda vai vencer e entra em "forecast".'
)


def position(user):
    """Onde o dinheiro está agora: saldo por conta e posição investida.

    Posição acumulada ignora recorte de período e conta todas as naturezas — é
    para isso que interna e ajuste existem. Filtrá-la pelo recorte da análise
    devolveria um saldo que não existe em extrato nenhum.
    """
    settled = Transaction.objects.filter(user=user, method__in=SETTLED_METHODS)

    by_account = {}
    for row in settled.order_by().values('account_id', 'account__description', 'type').annotate(total=Sum('value')):
        entry = by_account.setdefault(
            row['account_id'],
            {'id': row['account_id'], 'description': row['account__description'], 'income': ZERO, 'outcome': ZERO},
        )
        entry['income' if row['type'] == Type.IN else 'outcome'] += row['total']

    accounts = [{**entry, 'balance': money(entry['income'] - entry['outcome'])} for entry in by_account.values()]
    accounts.sort(key=lambda item: item['description'])

    investments = (
        Investment.objects.filter(user=user)
        .select_related('account', 'category')
        .annotate(
            applied=investment_total(Contribution),
            redeemed=investment_total(Redemption),
            yielded=investment_total(Yield),
        )
    )

    serialized_investments = []
    invested_total = ZERO
    for investment in investments:
        balance = investment.applied + investment.yielded - investment.redeemed
        invested_total += balance
        serialized_investments.append({
            'id': investment.id,
            'description': investment.description,
            'account': {'id': investment.account_id, 'description': str(investment.account)},
            'category': {'id': investment.category_id, 'description': investment.category_display},
            'applied': money(investment.applied),
            'yielded': money(investment.yielded),
            'redeemed': money(investment.redeemed),
            'balance': money(balance),
        })

    balance = sum((account['balance'] for account in accounts), ZERO)

    return {
        'scope': POSITION_SCOPE,
        'balance': money(balance),
        'invested': money(invested_total),
        'total': money(balance + invested_total),
        'accounts': accounts,
        'investments': serialized_investments,
    }


def forecast(user, today, filters):
    """Gasto no crédito já assumido que ainda vai vencer.

    Obedece aos filtros de dimensão — prever o cartão de uma conta ou categoria é
    pergunta legítima — mas não ao período nem às medidas: previsão é, por
    definição, saída em crédito daqui para a frente, e aceitar outro recorte
    devolveria sob o nome de previsão alguma outra coisa.
    """
    horizon = add_months(today, filters.forecast_months)
    window = Transaction.objects.filter(
        user=user,
        method=Method.CREDIT,
        type=Type.OUT,
        nature=Nature.REGULAR,
        datetime__date__gte=today,
        datetime__date__lte=horizon,
    ).filter(filters.dimensional_q())

    return {
        'scope': (
            f'Saída em crédito, natureza normal, com vencimento entre {today.isoformat()} e '
            f'{horizon.isoformat()}. Não se soma ao saldo: é o que sairá dele.'
        ),
        'total': money(window.aggregate(total=Sum('value'))['total']),
        'breakdowns': {name: breakdown(window, name, filters.top) for name in filters.group_by},
    }


def analytics(user, today, filters):
    """As seções pedidas em include, montadas sobre o mesmo recorte."""
    queryset = filters.queryset(user)
    payload = {'filters': filters.describe()}

    if 'summary' in filters.include:
        payload['summary'] = summarize(queryset)
    if 'breakdowns' in filters.include:
        payload['breakdowns'] = {name: breakdown(queryset, name, filters.top) for name in filters.group_by}
    if 'position' in filters.include:
        payload['position'] = position(user)
    if 'forecast' in filters.include:
        payload['forecast'] = forecast(user, today, filters)
    if 'transactions' in filters.include:
        payload['transactions'] = filters.transactions(queryset)

    return payload
