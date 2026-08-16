from datetime import timedelta
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.contrib.postgres.lookups import Unaccent
from django.db.models import Sum, Value
from django.db.models.functions import TruncMonth
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView, ListView
from django.views.generic.edit import CreateView, UpdateView, DeleteView
import reversion
from .forms import InstallmentForm, TransactionForm, TransferForm
from .models import Account, Category, Type, Method, Nature, Installment, Investment, Transaction, Transfer


MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def month_label(value):
    return f'{value.year} {MONTHS[value.month - 1]}'


def to_float(value):
    return float(value or Decimal('0.00'))


class LoginView(auth_views.LoginView):
    """Tela de login do sistema. O portal de administração segue com o login próprio."""

    template_name = 'app/login.html'
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    """Encerra a sessão e devolve o usuário para a tela de login."""

    next_page = 'app:login'


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

    def get_analytic_transactions(self, filters):
        """Recorte dos painéis: fora movimentação interna e ajuste, que só
        remanejam ou corrigem saldo e inflariam entrada e saída dos dois lados."""
        return self.get_transactions(filters).filter(nature=Nature.REGULAR)

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


class TransactionsListView(FilteredTransactionsMixin, OwnedListView):
    """Listagem completa: todos os métodos, com os mesmos filtros dos painéis."""

    model = Transaction
    template_name = 'app/transactions_list.html'
    paginate_by = 25

    def get_filters(self):
        filters = super().get_filters()
        filters['type'] = [v for v in self.request.GET.getlist('type') if v in Type.values]
        filters['method'] = [v for v in self.request.GET.getlist('method') if v in Method.values]
        filters['search'] = self.request.GET.get('search', '').strip()
        return filters

    def get_queryset(self):
        filters = self.get_filters()
        queryset = self.get_transactions(filters)

        if filters['type']:
            queryset = queryset.filter(type__in=filters['type'])
        if filters['method']:
            queryset = queryset.filter(method__in=filters['method'])
        if filters['search']:
            queryset = queryset.annotate(
                description_unaccent=Unaccent('description'),
            ).filter(description_unaccent__icontains=Unaccent(Value(filters['search'])))

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

        # A busca por descrição só faz sentido aqui: os painéis agregam valores,
        # não listam as descrições que o filtro recortaria.
        context['search_enabled'] = True

        # Formulários dos modais de criação. Na edição o JS preenche os campos
        # a partir dos data-attributes da linha, sem ida extra ao servidor.
        context['form'] = TransactionForm(user=self.request.user)
        context['installment_form'] = InstallmentForm(user=self.request.user)
        context['transfer_form'] = TransferForm(user=self.request.user)
        return context


class ModalWriteMixin(LoginRequiredMixin):
    """Base das telas de escrita, todas servidas pelos modais da listagem.

    Não há template de formulário próprio: o GET volta para a lista, o POST
    inválido devolve os erros como mensagens e o sucesso retorna à página que o
    usuário estava vendo.
    """

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get(self, request, *args, **kwargs):
        # Estas rotas existem só para receber o POST dos modais; não há template
        # de formulário próprio. Um GET (link colado, F5, histórico) volta para
        # a lista em vez de estourar TemplateDoesNotExist.
        return redirect('app:transactions_list')

    def get_success_url(self):
        # Devolve o usuário para a listagem com os filtros e a página que ele
        # estava vendo, em vez de jogá-lo no topo da lista sem filtro.
        # A validação é a do próprio Django: um simples startswith('/') deixaria
        # passar '//evil.com', que o navegador lê como protocol-relative.
        back = self.request.POST.get('back')
        if back and url_has_allowed_host_and_scheme(
            back,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return back
        return reverse('app:transactions_list')

    def form_invalid(self, form):
        for errors in form.errors.values():
            for error in errors:
                messages.error(self.request, error)
        return redirect(self.get_success_url())


class RevisionCreateMixin:
    """Criação com trilha de auditoria, a mesma do admin (VersionAdmin).

    Sem o create_revision o histórico ficaria cego para o que sai destas telas.
    Cada view informa o rótulo do que criou, usado na mensagem e no comentário
    da revisão.
    """

    success_message = None
    revision_comment = 'Criado pela tela de transações.'

    def form_valid(self, form):
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment(self.revision_comment)
            response = super().form_valid(form)

        messages.success(self.request, self.success_message)
        return response


class TransactionWriteMixin(ModalWriteMixin):
    """Escrita de transações avulsas do próprio usuário."""

    model = Transaction
    form_class = TransactionForm

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user)


class TransactionCreateView(RevisionCreateMixin, TransactionWriteMixin, CreateView):
    """Cria uma transação avulsa para o usuário logado."""

    success_message = 'Transação criada com sucesso.'


class InstallmentCreateView(RevisionCreateMixin, ModalWriteMixin, CreateView):
    """Cria um parcelamento, que por sua vez gera as transações das parcelas.

    Só a criação é exposta: alterar o parcelamento depois exigiria regerar as
    parcelas, e isso continua sendo assunto do admin.
    """

    model = Installment
    form_class = InstallmentForm
    success_message = 'Parcelamento criado com sucesso.'


class TransferCreateView(RevisionCreateMixin, ModalWriteMixin, CreateView):
    """Cria uma transferência, que gera o par de transações (saída e entrada)."""

    model = Transfer
    form_class = TransferForm
    success_message = 'Transferência criada com sucesso.'


class DerivedProtectedMixin:
    """Bloqueia a edição de transações geradas por um registro de origem: os
    valores delas derivam do parcelamento, da transferência ou do investimento,
    e mexer numa perna isolada deixaria o conjunto inconsistente.

    A exclusão não passa por aqui: ela apaga o registro de origem inteiro,
    que é o gesto coerente com o que o usuário vê na tela.
    """

    def get_object(self, queryset=None):
        transaction = super().get_object(queryset)
        if transaction.is_derived:
            raise PermissionDenied('Transações de parcelamento, transferência ou investimento são editadas pelo registro de origem, no portal de administração.')
        return transaction


class TransactionUpdateView(DerivedProtectedMixin, TransactionWriteMixin, UpdateView):
    """Edita uma transação avulsa do usuário logado."""

    def form_valid(self, form):
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment('Editado pela tela de transações.')
            response = super().form_valid(form)

        messages.success(self.request, 'Transação atualizada com sucesso.')
        return response


class TransactionDeleteView(TransactionWriteMixin, DeleteView):
    """Apaga uma transação do usuário logado.

    A transação avulsa se apaga sozinha. A que veio de um parcelamento ou de
    uma transferência apaga o registro de origem inteiro, e o CASCADE leva as
    demais transações dele junto: remover uma parcela isolada deixaria o
    parcelamento com um buraco, e uma perna de transferência sozinha viraria
    entrada ou saída de dinheiro que não existiu.

    Investimento continua fora: a origem dele acumula aplicações, resgates e
    rendimentos, e apagar tudo isso a partir de uma transação seria destrutivo
    demais para o gesto feito na tela.
    """

    # O DeleteView só precisa confirmar; herdar o TransactionForm faria o POST
    # ser validado contra campos que a confirmação nem envia.
    form_class = forms.Form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('user', None)
        kwargs.pop('instance', None)
        return kwargs

    def get_object(self, queryset=None):
        transaction = super().get_object(queryset)
        if transaction.investment_id:
            raise PermissionDenied('Transações de investimento são removidas pelo registro de origem, no portal de administração.')
        return transaction

    def form_valid(self, form):
        # O alvo real da remoção: a origem, quando existe, ou a própria
        # transação. O CASCADE das transações filhas cuida do resto. A mensagem
        # nomeia o que saiu junto, senão o usuário clica numa linha e vê várias
        # desaparecerem sem explicação.
        origin, message = self.object.deletable_origin
        target = origin or self.object

        # Revisão antes de apagar: guarda o último estado e o autor da remoção.
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment('Removido pela tela de transações.')
            reversion.add_to_revision(target)

        target.delete()

        messages.success(self.request, message or 'Transação removida com sucesso.')
        return redirect(self.get_success_url())
