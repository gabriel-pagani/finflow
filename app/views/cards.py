from django.db.models import Case, F, Q, Sum, When
from django.utils import timezone
from django.views.generic.edit import CreateView, UpdateView

from ..forms.finance import CardForm
from ..models import Card, Method, Type
from .mixins import ModalDeleteView, ModalWriteMixin, OwnedListView
from .transactions import form_options


class CardsListView(OwnedListView):
    model = Card
    template_name = 'app/cards_list.html'

    def get_queryset(self):
        # Limite usado: as compras que ainda vão vencer (as parcelas futuras
        # são transações com o vencimento delas); o estorno no cartão devolve.
        # Não confere se a fatura vencida foi paga.
        upcoming = Q(transactions__method=Method.CREDIT, transactions__effective_at__gte=timezone.localdate())
        signed = Case(When(transactions__type=Type.IN, then=-F('transactions__value')), default=F('transactions__value'))
        # Com agregação o Django ignora o ordering do Meta: a ordem vai explícita.
        return (
            super().get_queryset().select_related('account')
            .annotate(used=Sum(signed, filter=upcoming, default=0))
            .order_by(*Card._meta.ordering)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CardForm(user=self.request.user)
        context['form_options'] = form_options(self.request.user)
        return context


class CardWriteMixin(ModalWriteMixin):
    model = Card
    form_class = CardForm
    list_route = 'app:cards_list'


class CardCreateView(CardWriteMixin, CreateView):
    success_message = 'Cartão cadastrado com sucesso.'


class CardUpdateView(CardWriteMixin, UpdateView):
    success_message = 'Cartão atualizado com sucesso. O ciclo novo vale para as próximas compras; transações já feitas mantêm a data que tinham.'


class CardDeleteView(ModalDeleteView):
    model = Card
    list_route = 'app:cards_list'
    success_message = 'Cartão removido com sucesso.'
