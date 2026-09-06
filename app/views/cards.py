"""As telas do cartão: cadastro, não lançamento.

O cartão é consultado por toda compra no crédito e criado uma vez só, e é isso
que o separa da listagem de transações.
"""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic.edit import CreateView, DeleteView, UpdateView
import reversion

from ..forms import CardForm
from ..models import Card
from .mixins import ModalWriteMixin, OwnedListView, RevisionCreateMixin


class CardsListView(OwnedListView):
    """Cartões do usuário logado, com criação e edição pelos modais da página.

    Fica fora da listagem de transações porque é cadastro, não lançamento: o
    cartão é consultado por toda compra no crédito, e não se cria um a cada
    compra.
    """

    model = Card
    template_name = 'app/cards_list.html'
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().select_related('account')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CardForm(user=self.request.user)
        return context


class CardWriteMixin(ModalWriteMixin):
    """Escrita dos cartões do próprio usuário."""

    model = Card
    form_class = CardForm
    list_route = 'app:cards_list'

    def get_queryset(self):
        return Card.objects.filter(user=self.request.user)


class CardCreateView(RevisionCreateMixin, CardWriteMixin, CreateView):
    success_message = 'Cartão cadastrado com sucesso.'
    revision_comment = 'Criado pela tela de cartões.'


class CardUpdateView(CardWriteMixin, UpdateView):
    """Edita um cartão do usuário logado.

    Mudar o ciclo não remexe no que já foi lançado: as transações guardam a
    data que valia quando foram criadas, e recalculá-las mudaria faturas que o
    usuário já conferiu. O ciclo novo vale das próximas compras em diante.
    """

    def form_valid(self, form):
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment('Editado pela tela de cartões.')
            response = super().form_valid(form)

        messages.success(self.request, 'Cartão atualizado com sucesso. O ciclo novo vale para as próximas compras; lançamentos já feitos mantêm a data que tinham.')
        return response


class CardDeleteView(CardWriteMixin, DeleteView):
    """Apaga um cartão do usuário logado, se nada depender dele.

    O cartão em uso é protegido: apagá-lo levaria junto, por PROTECT, a decisão
    de data de transações, parcelamentos e assinaturas já lançados. Quem quer
    parar de usar um cartão pode simplesmente deixar de escolhê-lo.
    """

    # Como no DeleteView de transação: a confirmação só precisa do POST, e
    # herdar o CardForm faria validar campos que ela nem envia.
    form_class = forms.Form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('user', None)
        kwargs.pop('instance', None)
        return kwargs

    def form_valid(self, form):
        if self.object.in_use:
            messages.error(self.request, f'O cartão {self.object} não pode ser removido: há transações, parcelamentos ou assinaturas lançados nele.')
            return redirect(self.get_success_url())

        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment('Removido pela tela de cartões.')
            reversion.add_to_revision(self.object)

        self.object.delete()

        messages.success(self.request, 'Cartão removido com sucesso.')
        return redirect(self.get_success_url())
