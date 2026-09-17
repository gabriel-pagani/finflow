from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView
from django.views.generic.edit import DeleteView

from ..models import Account, Category, Nature, Transaction


# Valor do filtro para as transações sem categoria. Não é um pk, então não
# colide com categoria nenhuma, e sai na URL legível.
UNCATEGORIZED = 'none'


class FilteredTransactionsMixin(LoginRequiredMixin):
    methods = None

    def get_filters(self):
        get = self.request.GET
        today = timezone.localdate()

        return {
            'start': get.get('start') or today.replace(month=1, day=1).isoformat(),
            'end': get.get('end') or today.replace(month=12, day=31).isoformat(),
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

    def get_base_transactions(self, filters):
        queryset = self.get_scoped_transactions().select_related('account', 'category')

        if filters['account']:
            queryset = queryset.filter(account_id__in=filters['account'])

        return queryset

    def get_transactions(self, filters):
        queryset = self.get_base_transactions(filters).filter(
            effective_at__gte=filters['start'],
            effective_at__lte=filters['end'],
        )

        if filters['category']:
            chosen = Q(category_id__in=[value for value in filters['category'] if value != UNCATEGORIZED])
            if UNCATEGORIZED in filters['category']:
                chosen |= Q(category__isnull=True)
            queryset = queryset.filter(chosen)

        return queryset

    def get_analytic_transactions(self, filters):
        return self.get_transactions(filters).filter(nature=Nature.REGULAR)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filters'] = self.get_filters()
        scoped = self.get_scoped_transactions()
        context['accounts'] = Account.objects.filter(pk__in=scoped.values('account_id'))
        context['categories'] = Category.objects.filter(pk__in=scoped.values('category_id'))
        # Sem categoria também é uma escolha: sem esta opção, marcar categorias
        # deixaria de fora, sempre, o que não tem nenhuma.
        uncategorized = scoped.filter(category__isnull=True).exists()
        context['uncategorized_choices'] = [(UNCATEGORIZED, 'Categoria Não Identificada')] if uncategorized else []
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
