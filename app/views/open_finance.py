"""Revisão do que veio do banco: ajustar no modal e lançar, ou descartar."""

from datetime import timedelta
from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView
from django.views.generic.edit import CreateView

from ..forms.finance import InstallmentForm, TransactionForm
from ..models import Card, Installment, Method, Transaction
from ..models.bank import BankConnection, BankTransaction
from ..pluggy import PluggyError, sync
from .mixins import ModalWriteMixin
from .transactions import form_options


SIMILAR_WINDOW = timedelta(days=3)
SYNC_ERROR = 'Não foi possível buscar as transações no banco agora. Tente de novo em alguns minutos.'


def sync_connections(request, force=False):
    """Devolve quantas transações novas chegaram, ou None se a busca falhou."""
    new = 0
    for connection in BankConnection.objects.filter(user=request.user):
        if force or connection.is_stale:
            try:
                new += sync(connection)
            except (PluggyError, KeyError):
                messages.error(request, SYNC_ERROR)
                return None
    return new


def suggest_category(user, description):
    # A última categoria usada numa transação de mesma descrição. A descrição
    # que o banco manda se repete (o mercado de sempre), então o histórico das
    # aprovações vai ensinando.
    return (
        Transaction.objects
        .filter(user=user, description__iexact=description, category__isnull=False)
        .order_by('-id')
        .values_list('category_id', flat=True)
        .first()
    )


def installment_total(row):
    # O banco não manda o total, mas manda todas as parcelas: a 1ª leva a sobra
    # dos centavos e as outras são iguais, como no parcelamento do FinFlow.
    parcels = {item.parcel: item.value for item in BankTransaction.objects.filter(bank_account_id=row.bank_account_id, purchase_key=row.purchase_key)}
    other = next((value for number, value in parcels.items() if number != 1), row.value)
    return parcels.get(1, other) + other * (row.parcels - 1)


def review_rows(user):
    """Uma linha por lançamento: a compra parcelada aparece uma vez só."""
    cards = {(card.account_id, card.last_digits): card for card in Card.objects.filter(user=user, is_cancelled=False)}
    rows, seen = [], set()

    pending = (
        BankTransaction.objects
        .filter(user=user, status=BankTransaction.Status.PENDING)
        .select_related('bank_account__connection__account')
        # A parcela mais baixa representa a compra: é ela que traz o horário
        # da compra. A ordem da tela sai do sort no fim.
        .order_by('parcel', '-id')
    )
    for row in pending:
        if row.purchase_key:
            key = (row.bank_account_id, row.purchase_key)
            if key in seen:
                continue
            seen.add(key)

        account = row.bank_account.connection.account
        card = cards.get((account.pk if account else None, row.card_digits)) if row.method == Method.CREDIT else None
        description = row.purchase_key.split('|', 2)[2] if row.purchase_key else row.description

        item = {
            'row': row,
            'kind': 'installment' if row.purchase_key else 'transaction',
            'account': account.pk if account else '',
            'card': card.pk if card else '',
            'category': suggest_category(user, description) or '',
            'description': description,
        }
        if row.purchase_key:
            item.update(value=installment_total(row), occurred_at=row.purchase_date, installments=row.parcels)
            similar = Installment.objects.filter(user=user, installments=row.parcels, occurred_at__range=(row.purchase_date - SIMILAR_WINDOW, row.purchase_date + SIMILAR_WINDOW))
        else:
            item.update(value=row.value, occurred_at=row.occurred_at)
            similar = Transaction.objects.filter(user=user, type=row.type, value=row.value, occurred_at__range=(row.occurred_at - SIMILAR_WINDOW, row.occurred_at + SIMILAR_WINDOW), bank_transactions__isnull=True)
        # Avisa, não barra: quem sabe se já lançou à mão é o usuário.
        item['similar'] = similar.order_by('-occurred_at').first()
        rows.append(item)

    # A compra parcelada entra pela data da compra, não pela da parcela; no
    # mesmo dia, vale o horário em que o banco registrou.
    rows.sort(key=lambda item: (item['occurred_at'], item['row'].moment.timestamp() if item['row'].moment else 0), reverse=True)
    return rows


class OpenFinanceView(LoginRequiredMixin, TemplateView):
    template_name = 'app/open_finance.html'

    def get(self, request, *args, **kwargs):
        sync_connections(request)
        return super().get(request, *args, **kwargs)

    # "Buscar agora": a busca automática espera 15 minutos entre uma e outra.
    # Diz o resultado, senão o clique parece não ter feito nada quando o banco
    # não tem novidade.
    def post(self, request, *args, **kwargs):
        new = sync_connections(request, force=True)
        if new == 1:
            messages.success(request, '1 transação nova do banco.')
        elif new:
            messages.success(request, f'{new} transações novas do banco.')
        elif new == 0:
            messages.success(request, 'Nada novo no banco.')
        return redirect('app:open_finance')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['rows'] = review_rows(user)
        context['form'] = TransactionForm(user=user)
        context['installment_form'] = InstallmentForm(user=user)
        context['form_options'] = form_options(user)
        return context


class OpenFinanceTransactionApproveView(ModalWriteMixin, CreateView):
    list_route = 'app:open_finance'

    @cached_property
    def row(self):
        return get_object_or_404(BankTransaction, pk=self.kwargs['pk'], user=self.request.user, status=BankTransaction.Status.PENDING)

    def get_form_class(self):
        return InstallmentForm if self.row.purchase_key else TransactionForm

    @property
    def success_message(self):
        return 'Parcelamento lançado com sucesso.' if self.row.purchase_key else 'Transação lançada com sucesso.'

    def form_valid(self, form):
        # O modal prende a conta; isto barra o POST montado à mão.
        account = self.row.bank_account.connection.account
        if account and form.cleaned_data['account'] != account:
            form.add_error('account', f'A conta desta transação é a da conexão: {account}.')
            return self.form_invalid(form)

        with transaction.atomic():
            response = super().form_valid(form)

            if self.row.purchase_key:
                self.row.group().update(status=BankTransaction.Status.APPROVED, installment=self.object)
            else:
                self.row.group().update(status=BankTransaction.Status.APPROVED, transaction=self.object)
        return response


class OpenFinanceTransactionDiscardView(LoginRequiredMixin, View):
    def post(self, request, pk):
        row = get_object_or_404(BankTransaction, pk=pk, user=request.user, status=BankTransaction.Status.PENDING)
        row.group().update(status=BankTransaction.Status.DISCARDED)
        messages.success(request, 'Transação descartada.')
        return redirect('app:open_finance')
