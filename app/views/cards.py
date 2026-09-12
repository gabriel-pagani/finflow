from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic.edit import CreateView, UpdateView

from ..forms import CardForm
from ..models import Card
from .mixins import ModalDeleteView, ModalWriteMixin, OwnedListView


class CardsListView(OwnedListView):
    model = Card
    template_name = 'app/cards_list.html'

    def get_queryset(self):
        return super().get_queryset().select_related('account')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CardForm(user=self.request.user)
        return context


class CardWriteMixin(ModalWriteMixin):
    model = Card
    form_class = CardForm
    list_route = 'app:cards_list'


class CardCreateView(CardWriteMixin, CreateView):
    success_message = 'Cartão cadastrado com sucesso.'


class CardUpdateView(CardWriteMixin, UpdateView):
    success_message = 'Cartão atualizado com sucesso. O ciclo novo vale para as próximas compras; lançamentos já feitos mantêm a data que tinham.'


class CardDeleteView(ModalDeleteView):
    model = Card
    list_route = 'app:cards_list'
    success_message = 'Cartão removido com sucesso.'

    def form_valid(self, form):
        if self.object.transactions.exists() or self.object.installments.exists():
            messages.error(self.request, f'O cartão {self.object} não pode ser removido: há transações ou parcelamentos lançados nele.')
            return redirect(self.get_success_url())

        return super().form_valid(form)
