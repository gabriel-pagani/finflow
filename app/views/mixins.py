"""Views compartilhadas para listas e formulários modais."""

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView
from django.views.generic.edit import DeleteView

# Mantém o caminho de importação anterior para integrações existentes.
from .filtering import FilteredTransactionsMixin, build_panel, distinct_values, parse_date


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
