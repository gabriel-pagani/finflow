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

As quatro seções que o assistente pede — opções, posição, previsão e análise —
são o que este pacote expõe. Filtro, agregação e serialização são a maquinaria
por trás delas, e ficam nos módulos ao lado: quem chama daqui de fora nunca
precisou saber que existem.
"""

from django.db.models import Sum

from ...models import Account, BusinessRule, Card, Category, Contribution, Investment, Method, Nature, Redemption, Transaction, Type, Yield
from ...utils.dates import add_months
from ...utils.formatting import ZERO, to_money
from .aggregates import breakdown, investment_total, summarize
from .constants import NO_CARD, SETTLED_METHODS, UNCATEGORIZED
from .filters import AnalyticsFilters, FilterError
from .serializers import json_default, serialize_card, serialize_choices, serialize_transaction


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

    accounts = [{**entry, 'balance': to_money(entry['income'] - entry['outcome'])} for entry in by_account.values()]
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
            'applied': to_money(investment.applied),
            'yielded': to_money(investment.yielded),
            'redeemed': to_money(investment.redeemed),
            'balance': to_money(balance),
        })

    balance = sum((account['balance'] for account in accounts), ZERO)

    return {
        'scope': POSITION_SCOPE,
        'balance': to_money(balance),
        'invested': to_money(invested_total),
        'total': to_money(balance + invested_total),
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
        'total': to_money(window.aggregate(total=Sum('value'))['total']),
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


__all__ = [
    'AnalyticsFilters',
    'FilterError',
    'NO_CARD',
    'SETTLED_METHODS',
    'UNCATEGORIZED',
    'analytics',
    'breakdown',
    'forecast',
    'json_default',
    'options',
    'position',
    'serialize_card',
    'serialize_choices',
    'serialize_transaction',
    'summarize',
]
