from django.contrib.postgres.lookups import Unaccent
from django.core.exceptions import PermissionDenied
from django.db.models import Value
from django.views.generic.edit import CreateView, UpdateView

from ..forms import InstallmentForm, TransactionForm, TransferForm
from ..models import BusinessRule, Card, Installment, Method, Nature, Transaction, Transfer, Type
from .mixins import FilteredTransactionsMixin, ModalDeleteView, ModalWriteMixin, OwnedListView


# As combinações que cada conta aceita, para o modal só oferecer o que o
# servidor aprovaria. Quem valida continua sendo o model: isto poupa o usuário
# de montar um lançamento impossível, não substitui a checagem.
#
# Os ids saem como texto porque o value de um <option> é texto, e comparar com
# número exigiria converter dos dois lados no navegador.
def form_options(user):
    rules = {}
    for account_id, type, method in BusinessRule.objects.values_list('account_id', 'type', 'method'):
        rules.setdefault(str(account_id), {}).setdefault(type, []).append(method)

    return {
        'rules': rules,
        'cards': {str(pk): str(account_id) for pk, account_id in Card.objects.filter(user=user).values_list('pk', 'account_id')},
        # O parcelamento não pergunta tipo nem método: é sempre saída no
        # crédito, e só as contas que aceitam essa combinação servem.
        'fixed': {'installment': {'type': Installment.TYPE, 'method': Installment.METHOD}},
    }


class TransactionsListView(FilteredTransactionsMixin, OwnedListView):
    model = Transaction
    template_name = 'app/transactions_list.html'

    def get_filters(self):
        get = self.request.GET
        filters = super().get_filters()
        filters['type'] = [value for value in get.getlist('type') if value in Type.values]
        filters['method'] = [value for value in get.getlist('method') if value in Method.values]
        filters['nature'] = [value for value in get.getlist('nature') if value in Nature.values]
        filters['search'] = get.get('search', '').strip()
        return filters

    def get_queryset(self):
        filters = self.get_filters()
        queryset = self.get_transactions(filters)

        if filters['type']:
            queryset = queryset.filter(type__in=filters['type'])
        if filters['method']:
            queryset = queryset.filter(method__in=filters['method'])
        if filters['nature']:
            queryset = queryset.filter(nature__in=filters['nature'])
        if filters['search']:
            queryset = queryset.annotate(
                description_unaccent=Unaccent('description'),
            ).filter(description_unaccent__icontains=Unaccent(Value(filters['search'])))

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['types'] = Type.choices
        context['method_choices'] = Method.choices
        context['nature_choices'] = Nature.choices
        context['search_enabled'] = True

        # Formulários dos modais de criação. Na edição o JS preenche os campos
        # a partir dos data-attributes da linha, sem ida extra ao servidor.
        context['form'] = TransactionForm(user=self.request.user)
        context['installment_form'] = InstallmentForm(user=self.request.user)
        context['transfer_form'] = TransferForm(user=self.request.user)
        context['form_options'] = form_options(self.request.user)
        return context


class TransactionWriteMixin(ModalWriteMixin):
    model = Transaction
    form_class = TransactionForm


class TransactionCreateView(TransactionWriteMixin, CreateView):
    success_message = 'Transação criada com sucesso.'


class TransactionUpdateView(TransactionWriteMixin, UpdateView):
    success_message = 'Transação atualizada com sucesso.'

    def get_object(self, queryset=None):
        transaction = super().get_object(queryset)
        if transaction.is_derived:
            raise PermissionDenied('Transações de parcelamento ou transferência são editadas pelo registro de origem, no portal de administração.')
        return transaction


class TransactionDeleteView(ModalDeleteView):
    model = Transaction
    success_message = 'Transação removida com sucesso.'

    def get_target(self):
        return self.object.installment or self.object.transfer or self.object

    def get_success_message(self):
        if self.object.installment_id:
            return 'Parcelamento removido com sucesso, junto de todas as suas parcelas.'
        if self.object.transfer_id:
            return 'Transferência removida com sucesso, junto das duas transações que ela gerou.'
        return self.success_message


# Parcelamento e transferência não têm tela própria: são registros de origem
# criados pelos modais da listagem, e o que o usuário vê depois são as
# transações que eles geraram. Só a criação é exposta — alterá-los exigiria
# regerar as transações filhas, e isso segue sendo assunto do admin.
class InstallmentCreateView(ModalWriteMixin, CreateView):
    model = Installment
    form_class = InstallmentForm
    success_message = 'Parcelamento criado com sucesso.'


class TransferCreateView(ModalWriteMixin, CreateView):
    model = Transfer
    form_class = TransferForm
    success_message = 'Transferência criada com sucesso.'
