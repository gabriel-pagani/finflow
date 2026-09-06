"""Os dois painéis: o que já saiu da conta e o que ainda vai vencer.

São a mesma leitura do mesmo conjunto de transações, recortada por método. Um
olha o débito, que é dinheiro que saiu; o outro o crédito, que é gasto assumido
com data marcada. Por isso dividem os filtros e diferem só no que agregam.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.views.generic import TemplateView

from ..models import Investment, Method, Type
from ..utils.charts import month_label, to_float
from .mixins import FilteredTransactionsMixin, SubscriptionSyncMixin


class OverviewView(SubscriptionSyncMixin, FilteredTransactionsMixin, TemplateView):
    """Painel do realizado: débito e não se aplica, o dinheiro que já saiu da conta."""

    template_name = 'app/overview.html'
    methods = [Method.DEBIT, Method.NOT_APPLICABLE]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transactions = self.get_analytic_transactions(context['filters'])

        totals = {
            row['type']: row['total']
            for row in transactions.values('type').annotate(total=Sum('value'))
        }
        income = to_float(totals.get(Type.IN))
        outcome = to_float(totals.get(Type.OUT))

        # Saldo é posição acumulada: ignora o recorte de período e de categoria,
        # e conta toda natureza — é justamente para ele que interna e ajuste existem.
        balance_totals = {
            row['type']: row['total']
            for row in self.get_base_transactions(context['filters']).values('type').annotate(total=Sum('value'))
        }
        balance = to_float(balance_totals.get(Type.IN)) - to_float(balance_totals.get(Type.OUT))

        # Saldo investido é posição acumulada, como o card de saldo: só o
        # filtro de conta se aplica, não o de período nem o de categoria.
        investments = Investment.objects.filter(user=self.request.user)
        if context['filters']['account']:
            investments = investments.filter(account_id__in=context['filters']['account'])

        invested = Decimal('0.00')
        for investment in investments:
            invested += investment.balance

        by_month = (
            transactions
            .annotate(month=TruncMonth('datetime'))
            .values('month', 'type')
            .annotate(total=Sum('value'))
            .order_by('month')
        )

        months = sorted({row['month'] for row in by_month})
        income_series = {month: 0.0 for month in months}
        outcome_series = {month: 0.0 for month in months}
        for row in by_month:
            series = income_series if row['type'] == Type.IN else outcome_series
            series[row['month']] = to_float(row['total'])

        by_category = (
            transactions
            .filter(type=Type.OUT)
            .values('category__description')
            .annotate(total=Sum('value'))
            .order_by('-total')
        )

        context['cards'] = {
            'income': income,
            'outcome': outcome,
            'invested': to_float(invested),
            'balance': balance,
        }
        # As séries já saem daqui com nome e cor: o template só aponta o
        # elemento para este JSON, sem script inline para montá-las.
        context['chart_months'] = {
            'labels': [month_label(month) for month in months],
            'series': [
                {'name': 'Entrada', 'data': [income_series[month] for month in months], 'color': '#5aa469'},
                {'name': 'Saída', 'data': [outcome_series[month] for month in months], 'color': '#c0504d'},
            ],
        }
        context['chart_categories'] = [
            {'name': row['category__description'] or 'Categoria Não Identificada', 'value': to_float(row['total'])}
            for row in by_category
        ]
        return context


class ForecastView(SubscriptionSyncMixin, FilteredTransactionsMixin, TemplateView):
    """Painel de previsão: crédito, o gasto já assumido que ainda vai vencer."""

    template_name = 'app/forecast.html'
    methods = [Method.CREDIT]

    def get_filters(self):
        filters = super().get_filters()
        get = self.request.GET
        today = timezone.localdate()

        if not get.get('start'):
            filters['start'] = today.isoformat()
        if not get.get('end'):
            filters['end'] = (today + timedelta(days=365)).isoformat()

        return filters

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transactions = self.get_analytic_transactions(context['filters']).filter(type=Type.OUT)

        by_month = (
            transactions
            .annotate(month=TruncMonth('datetime'))
            .values('month')
            .annotate(total=Sum('value'))
            .order_by('month')
        )

        by_category = (
            transactions
            .values('category__description')
            .annotate(total=Sum('value'))
            .order_by('-total')
        )

        context['total'] = to_float(transactions.aggregate(total=Sum('value'))['total'])
        context['chart_months'] = {
            'labels': [month_label(row['month']) for row in by_month],
            'series': [
                {'name': 'Gasto Previsto', 'data': [to_float(row['total']) for row in by_month], 'color': '#c0504d'},
            ],
        }
        context['chart_categories'] = [
            {'name': row['category__description'] or 'Categoria Não Identificada', 'value': to_float(row['total'])}
            for row in by_category
        ]
        return context
