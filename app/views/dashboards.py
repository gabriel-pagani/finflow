from datetime import timedelta

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.views.generic import TemplateView

from ..models import Type
from ..scopes import FORECAST_METHODS, OVERVIEW_METHODS
from ..utils.charts import month_label, to_float
from .filtering import FilteredTransactionsMixin


INCOME_COLOR = '#5aa469'
OUTCOME_COLOR = '#c0504d'


def totals_by_type(queryset) -> tuple[float, float]:
    totals = {row['type']: row['total'] for row in queryset.values('type').annotate(total=Sum('value'))}

    return to_float(totals.get(Type.IN)), to_float(totals.get(Type.OUT))


def by_month(transactions, *keys):
    return (
        transactions
        .annotate(month=TruncMonth('effective_at'))
        .values('month', *keys)
        .annotate(total=Sum('value'))
        .order_by('month')
    )


def categories_series(transactions):
    rows = (
        transactions
        .values('category__description')
        .annotate(total=Sum('value'))
        .order_by('-total')
    )

    return [
        {'name': row['category__description'] or 'Categoria Não Identificada', 'value': to_float(row['total'])}
        for row in rows
    ]


class OverviewView(FilteredTransactionsMixin, TemplateView):
    template_name = 'app/overview.html'
    methods = OVERVIEW_METHODS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = context['filters']
        transactions = self.get_analytic_transactions(filters)

        income, outcome = totals_by_type(transactions)

        balance_income, balance_outcome = totals_by_type(self.get_base_transactions(filters))

        rows = by_month(transactions, 'type')
        months = sorted({row['month'] for row in rows})
        series = {Type.IN: dict.fromkeys(months, 0.0), Type.OUT: dict.fromkeys(months, 0.0)}
        for row in rows:
            series[row['type']][row['month']] = to_float(row['total'])

        context['cards'] = {
            'income': income,
            'outcome': outcome,
            'balance': balance_income - balance_outcome,
        }
        context['chart_months'] = {
            'labels': [month_label(month) for month in months],
            'series': [
                {'name': 'Entrada', 'data': [series[Type.IN][month] for month in months], 'color': INCOME_COLOR},
                {'name': 'Saída', 'data': [series[Type.OUT][month] for month in months], 'color': OUTCOME_COLOR},
            ],
        }
        context['chart_categories'] = categories_series(transactions.filter(type=Type.OUT))
        return context


class ForecastView(FilteredTransactionsMixin, TemplateView):
    template_name = 'app/forecast.html'
    methods = FORECAST_METHODS

    # A previsão olha para a frente: de hoje até daqui a um ano.
    def get_default_period(self, today):
        return today, today + timedelta(days=365)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transactions = self.get_analytic_transactions(context['filters']).filter(type=Type.OUT)

        rows = by_month(transactions)

        context['total'] = to_float(transactions.aggregate(total=Sum('value'))['total'])
        context['chart_months'] = {
            'labels': [month_label(row['month']) for row in rows],
            'series': [
                {'name': 'Gasto Previsto', 'data': [to_float(row['total']) for row in rows], 'color': OUTCOME_COLOR},
            ],
        }
        context['chart_categories'] = categories_series(transactions)
        return context
