from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.views.generic import TemplateView, ListView
from .models import Account, Category, Type, Method, Installment, Investment, Transaction


MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def month_label(value):
    return f'{value.year} {MONTHS[value.month - 1]}'


def to_float(value):
    return float(value or Decimal('0.00'))


class FilteredTransactionsMixin(LoginRequiredMixin):
    """Aplica os filtros da barra superior sobre as transações do usuário."""

    methods = None

    def get_id_list(self, name):
        """Lê um filtro de múltipla escolha, descartando valores não numéricos."""
        return [value for value in self.request.GET.getlist(name) if value.isdigit()]

    def get_filters(self):
        get = self.request.GET
        today = timezone.localdate()

        start = get.get('start') or today.replace(month=1, day=1).isoformat()
        end = get.get('end') or today.replace(month=12, day=31).isoformat()

        return {
            'start': start,
            'end': end,
            'account': self.get_id_list('account'),
            'category': self.get_id_list('category'),
        }

    def get_base_transactions(self, filters):
        """Transações do usuário sem recorte de período nem de categoria."""
        queryset = Transaction.objects.filter(user=self.request.user).select_related('account', 'category')

        if self.methods:
            queryset = queryset.filter(method__in=self.methods)
        if filters['account']:
            queryset = queryset.filter(account_id__in=filters['account'])

        return queryset

    def get_transactions(self, filters):
        queryset = self.get_base_transactions(filters)

        queryset = queryset.filter(datetime__date__gte=filters['start'], datetime__date__lte=filters['end'])

        if filters['category']:
            queryset = queryset.filter(category_id__in=filters['category'])

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filters'] = self.get_filters()
        context['accounts'] = Account.objects.all()
        context['categories'] = Category.objects.all()
        return context


class OverviewView(FilteredTransactionsMixin, TemplateView):
    """Painel do realizado: débito e não se aplica, o dinheiro que já saiu da conta."""

    template_name = 'app/overview.html'
    methods = [Method.DEBIT, Method.NOT_APPLICABLE]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transactions = self.get_transactions(context['filters'])

        totals = {
            row['type']: row['total']
            for row in transactions.values('type').annotate(total=Sum('value'))
        }
        income = to_float(totals.get(Type.IN))
        outcome = to_float(totals.get(Type.OUT))

        # Saldo é posição acumulada: ignora o recorte de período e de categoria.
        balance_totals = {
            row['type']: row['total']
            for row in self.get_base_transactions(context['filters']).values('type').annotate(total=Sum('value'))
        }
        balance = to_float(balance_totals.get(Type.IN)) - to_float(balance_totals.get(Type.OUT))

        invested = Decimal('0.00')
        for investment in Investment.objects.filter(user=self.request.user):
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
        context['chart_months'] = {
            'labels': [month_label(month) for month in months],
            'income': [income_series[month] for month in months],
            'outcome': [outcome_series[month] for month in months],
        }
        context['chart_categories'] = [
            {'name': row['category__description'] or 'Categoria Não Identificada', 'value': to_float(row['total'])}
            for row in by_category
        ]
        return context


class ForecastView(FilteredTransactionsMixin, TemplateView):
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
        transactions = self.get_transactions(context['filters']).filter(type=Type.OUT)

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
            'values': [to_float(row['total']) for row in by_month],
        }
        context['chart_categories'] = [
            {'name': row['category__description'] or 'Categoria Não Identificada', 'value': to_float(row['total'])}
            for row in by_category
        ]
        return context


class OwnedListView(LoginRequiredMixin, ListView):
    """Listagem somente leitura, restrita aos registros do usuário logado."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Mantém os demais parâmetros da URL ao trocar de página.
        params = self.request.GET.copy()
        params.pop('page', None)
        context['querystring'] = params.urlencode()

        return context


class TransactionListView(FilteredTransactionsMixin, OwnedListView):
    """Listagem completa: todos os métodos, com os mesmos filtros dos painéis."""

    model = Transaction
    template_name = 'app/transaction_list.html'
    paginate_by = 25

    def get_filters(self):
        filters = super().get_filters()
        filters['type'] = [v for v in self.request.GET.getlist('type') if v in Type.values]
        filters['method'] = [v for v in self.request.GET.getlist('method') if v in Method.values]
        return filters

    def get_queryset(self):
        filters = self.get_filters()
        queryset = self.get_transactions(filters)

        if filters['type']:
            queryset = queryset.filter(type__in=filters['type'])
        if filters['method']:
            queryset = queryset.filter(method__in=filters['method'])

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Total de todas as transações filtradas, não só as da página atual.
        totals = {
            row['type']: row['total']
            for row in self.object_list.values('type').annotate(total=Sum('value'))
        }
        context['total_income'] = to_float(totals.get(Type.IN))
        context['total_outcome'] = to_float(totals.get(Type.OUT))
        context['total_count'] = self.object_list.count()

        context['types'] = Type.choices
        context['method_choices'] = Method.choices
        return context
