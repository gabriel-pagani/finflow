from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView
from django.views.generic.edit import DeleteView
import reversion

from ..models import Account, Category, Nature, Transaction


class FilteredTransactionsMixin(LoginRequiredMixin):
    methods = None

    def get_filters(self):
        get = self.request.GET
        today = timezone.localdate()

        return {
            'start': get.get('start') or today.replace(month=1, day=1).isoformat(),
            'end': get.get('end') or today.replace(month=12, day=31).isoformat(),
            'account': [value for value in get.getlist('account') if value.isdigit()],
            'category': [value for value in get.getlist('category') if value.isdigit()],
        }

    def get_base_transactions(self, filters):
        queryset = Transaction.objects.filter(user=self.request.user).select_related('account', 'category')

        if self.methods:
            queryset = queryset.filter(method__in=self.methods)
        if filters['account']:
            queryset = queryset.filter(account_id__in=filters['account'])

        return queryset

    def get_transactions(self, filters):
        queryset = self.get_base_transactions(filters).filter(
            effective_at__gte=filters['start'],
            effective_at__lte=filters['end'],
        )

        if filters['category']:
            queryset = queryset.filter(category_id__in=filters['category'])

        return queryset

    def get_analytic_transactions(self, filters):
        return self.get_transactions(filters).filter(nature=Nature.REGULAR)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filters'] = self.get_filters()
        context['accounts'] = Account.objects.all()
        context['categories'] = Category.objects.all()
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
    revision_comment = ''

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
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment(self.revision_comment)
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

        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment(self.revision_comment)
            reversion.add_to_revision(target)

        message = self.get_success_message()
        target.delete()

        messages.success(self.request, message)
        return redirect(self.get_success_url())
