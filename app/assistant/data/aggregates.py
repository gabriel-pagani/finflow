"""A agregação da análise, e a forma única em que toda quebra sai.

Todo eixo devolve linhas iguais — {key, label, income, outcome, net, count} —,
e é isso que deixa o agente aprender a ler uma quebra e saber ler as outras.
Quebrar por mês e quebrar por categoria são recortes do mesmo conjunto: as
linhas de cada eixo somam sempre o mesmo total.
"""

from django.db.models import Count, DecimalField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek, TruncYear

from ...models import Method, Nature, Type
from ...utils.formatting import ZERO, to_money
from .constants import NO_CARD, UNCATEGORIZED


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
        'income': to_money(entry['income']),
        'outcome': to_money(entry['outcome']),
        'net': to_money(entry['income'] - entry['outcome']),
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

    income = to_money(rows.get(Type.IN, {}).get('total'))
    outcome = to_money(rows.get(Type.OUT, {}).get('total'))

    return {
        'income': income,
        'outcome': outcome,
        'net': to_money(income - outcome),
        'transactions': sum(row['count'] for row in rows.values()),
    }
