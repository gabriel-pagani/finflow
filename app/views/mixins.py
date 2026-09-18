from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView
from django.views.generic.edit import DeleteView

from ..models import Account, Category, Transaction
from ..scopes import ANALYTIC_NATURES


# Valor do filtro para as transações sem categoria. Não é um pk, então não
# colide com categoria nenhuma, e sai na URL legível.
UNCATEGORIZED = 'none'
UNCATEGORIZED_LABEL = 'Categoria Não Identificada'

# Como o painel ao vivo pede só as opções, na mesma URL da página. Sem isto
# precisaria de uma rota paralela por tela, e o recorte das opções poderia
# divergir do recorte que a tela mostra.
OPTIONS_PARAM = 'only'
OPTIONS_VALUE = 'filters'


def parse_date(value):
    """A data do filtro, se for mesmo uma data; None se veio vazia ou inválida.

    O valor vai direto para a consulta, e texto que não é data (?start=abc)
    derrubava a página com 500 em vez de cair no período padrão.
    """
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def distinct_values(queryset, field):
    """Os valores que sobraram numa coluna, como texto — a forma que sai no HTML.

    O `order_by()` limpa a ordenação padrão do model: com ela, as colunas de
    ordem entram no DISTINCT e o banco devolve uma linha por transação em vez
    de uma por valor.
    """
    return {str(value) for value in queryset.order_by().values_list(field, flat=True).distinct() if value is not None}


def build_panel(name, legend, empty, gender, entries, available, selected):
    """Monta um painel de filtro.

    `entries` são todos os valores que a página admite e `available` os que
    sobreviveram aos outros filtros. O que está marcado continua na lista mesmo
    sem ter sobrado nada: tirar da tela uma escolha do usuário mudaria o filtro
    sem ele pedir, e ele não veria por que o resultado ficou vazio.
    """
    options = [
        {
            'value': str(value),
            'label': str(label),
            'available': str(value) in available,
            'selected': str(value) in selected,
        }
        for value, label in entries
        if str(value) in available or str(value) in selected
    ]

    return {'name': name, 'legend': legend, 'empty': empty, 'gender': gender, 'options': options}


class FilteredTransactionsMixin(LoginRequiredMixin):
    methods = None

    # O período quando a URL não traz um válido: o ano corrente.
    def get_default_period(self, today):
        return today.replace(month=1, day=1), today.replace(month=12, day=31)

    def get_filters(self):
        get = self.request.GET
        start, end = self.get_default_period(timezone.localdate())

        return {
            'start': (parse_date(get.get('start')) or start).isoformat(),
            'end': (parse_date(get.get('end')) or end).isoformat(),
            'account': [value for value in get.getlist('account') if value.isdigit()],
            'category': [value for value in get.getlist('category') if value.isdigit() or value == UNCATEGORIZED],
        }

    # Tudo o que a página enxerga, antes de qualquer filtro escolhido: as
    # transações do usuário nos métodos que ela analisa. É daqui que saem tanto
    # os números quanto as opções de filtro, para que o filtro nunca ofereça o
    # que a página não olha.
    def get_scoped_transactions(self):
        queryset = Transaction.objects.filter(user=self.request.user)

        if self.methods:
            queryset = queryset.filter(method__in=self.methods)

        return queryset

    # `ignore` deixa de fora o filtro de uma dimensão. Serve para montar as
    # opções dela: com o próprio filtro aplicado, marcar uma conta esconderia
    # todas as outras e a escolha viraria uma armadilha sem volta.
    def get_base_transactions(self, filters, *, ignore=None):
        queryset = self.get_scoped_transactions().select_related('account', 'category')

        if filters['account'] and ignore != 'account':
            queryset = queryset.filter(account_id__in=filters['account'])

        return queryset

    def get_transactions(self, filters, *, ignore=None):
        queryset = self.get_base_transactions(filters, ignore=ignore).filter(
            effective_at__gte=filters['start'],
            effective_at__lte=filters['end'],
        )

        if filters['category'] and ignore != 'category':
            chosen = Q(category_id__in=[value for value in filters['category'] if value != UNCATEGORIZED])
            if UNCATEGORIZED in filters['category']:
                chosen |= Q(category__isnull=True)
            queryset = queryset.filter(chosen)

        return self.apply_extra_filters(queryset, filters, ignore)

    # Onde cada página encaixa os filtros que só ela tem, para que eles entrem
    # tanto no resultado quanto no recorte das opções das outras dimensões.
    def apply_extra_filters(self, queryset, filters, ignore):
        return queryset

    def get_analytic_transactions(self, filters):
        return self.get_transactions(filters).filter(nature__in=ANALYTIC_NATURES)

    # Os painéis das dimensões que só algumas páginas têm.
    def get_extra_panels(self, filters):
        return []

    def get_filter_panels(self, filters):
        scoped = self.get_scoped_transactions()
        accounts = self.get_transactions(filters, ignore='account')
        categories = self.get_transactions(filters, ignore='category')

        # Sem categoria também é uma escolha: sem esta opção, marcar categorias
        # deixaria de fora, sempre, o que não tem nenhuma.
        entries = list(Category.objects.filter(pk__in=scoped.values('category_id')).values_list('pk', 'description'))
        available = distinct_values(categories, 'category_id')
        if scoped.filter(category__isnull=True).exists():
            entries.append((UNCATEGORIZED, UNCATEGORIZED_LABEL))
        if categories.filter(category__isnull=True).exists():
            available.add(UNCATEGORIZED)

        return [
            build_panel(
                'account', 'Conta', 'Todas', 'f',
                Account.objects.filter(pk__in=scoped.values('account_id')).values_list('pk', 'description'),
                distinct_values(accounts, 'account_id'), filters['account'],
            ),
            build_panel('category', 'Categoria', 'Todas', 'f', entries, available, filters['category']),
            *self.get_extra_panels(filters),
        ]

    # O painel ao vivo pede as opções para a própria página, com os filtros que
    # a pessoa acabou de mexer. Responder antes do super() poupa montar gráfico
    # e paginação que ninguém vai ler.
    def get(self, request, *args, **kwargs):
        if request.GET.get(OPTIONS_PARAM) == OPTIONS_VALUE:
            return JsonResponse({'panels': self.get_filter_panels(self.get_filters())})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filters'] = self.get_filters()
        context['panels'] = self.get_filter_panels(context['filters'])
        return context


class OwnedListView(LoginRequiredMixin, ListView):
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        context['querystring'] = params.urlencode()
        return context


class ModalWriteMixin(LoginRequiredMixin):
    list_route = 'app:transactions_list'
    success_message = ''

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get(self, request, *args, **kwargs):
        return redirect(self.list_route)

    def get_success_url(self):
        back = self.request.POST.get('back')
        if back and url_has_allowed_host_and_scheme(
            back,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return back
        return reverse(self.list_route)

    def form_invalid(self, form):
        for errors in form.errors.values():
            for error in errors:
                messages.error(self.request, error)
        return redirect(self.get_success_url())

    def form_valid(self, form):
        response = super().form_valid(form)

        messages.success(self.request, self.success_message)
        return response


class ModalDeleteView(ModalWriteMixin, DeleteView):
    form_class = forms.Form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('user', None)
        kwargs.pop('instance', None)
        return kwargs

    def get_target(self):
        return self.object

    def get_success_message(self):
        return self.success_message

    def form_valid(self, form):
        target = self.get_target()

        message = self.get_success_message()
        try:
            target.delete()
        except ValidationError as error:
            for text in error.messages:
                messages.error(self.request, text)
            return redirect(self.get_success_url())

        messages.success(self.request, message)
        return redirect(self.get_success_url())
