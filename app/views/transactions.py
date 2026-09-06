"""A listagem de transações e as telas que escrevem nela.

Parcelamento e transferência aparecem aqui, e não em módulos próprios, porque
não têm tela: são registros de origem criados pelos modais desta lista, e o que
o usuário vê depois são as transações que eles geraram.
"""

from django import forms
from django.contrib import messages
from django.contrib.postgres.lookups import Unaccent
from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Value
from django.shortcuts import redirect
from django.views.generic.edit import CreateView, DeleteView, UpdateView
import reversion

from ..forms import InstallmentForm, TransactionForm, TransferForm
from ..models import Installment, Method, Transaction, Transfer, Type
from ..utils.charts import to_float
from .mixins import (
    DerivedProtectedMixin,
    FilteredTransactionsMixin,
    ModalWriteMixin,
    OwnedListView,
    RevisionCreateMixin,
    SubscriptionSyncMixin,
)


class TransactionsListView(SubscriptionSyncMixin, FilteredTransactionsMixin, OwnedListView):
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
