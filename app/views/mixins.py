"""As peças que as telas compartilham: filtro, propriedade, modal e auditoria.

Nenhuma delas é uma tela. São o comportamento repetido entre as de transação,
cartão e assinatura — e ficam juntas aqui porque é assim que se enxerga o que
uma view nova ganha de graça ao herdá-las.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView
import reversion

from ..models import Account, Category, Nature, Subscription, Transaction


class SubscriptionSyncMixin:
    """Lança as cobranças de assinatura vencidas antes de desenhar a tela.

    O cron é quem tem a obrigação de rodar todo dia; isto aqui é a segunda
    perna. Máquina desligada no horário, container recriado, cron que ninguém
    instalou: em qualquer um desses casos quem abre o sistema veria o mês sem a
    cobrança que já venceu, e é justamente essa tela que ele abriu para
    conferir.

    Sai barato porque a consulta é seletiva: assinatura com a competência do mês
    já lançada nem é carregada, e no dia a dia isso é toda a lista.
    """

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            Subscription.generate_due(user=request.user)
        return super().get(request, *args, **kwargs)


class FilteredTransactionsMixin(LoginRequiredMixin):
    """Aplica os filtros da barra superior sobre as transações do usuário."""

    methods = None

    def get_id_list(self, name):
        """Lê um filtro de múltipla escolha, descartando valores não numéricos."""
        return [value for value in self.request.GET.getlist(name) if value.isdigit()]

    def get_filters(self):
        get = self.request.GET
        today = timezone.localdate()

        start = get.get('start') or today.replace(month=1, day=1).isoformat()
        end = get.get('end') or today.replace(month=12, day=31).isoformat()

        return {
            'start': start,
            'end': end,
            'account': self.get_id_list('account'),
            'category': self.get_id_list('category'),
        }

    def get_base_transactions(self, filters):
        """Transações do usuário sem recorte de período nem de categoria."""
        queryset = Transaction.objects.filter(user=self.request.user).select_related('account', 'category')

        if self.methods:
            queryset = queryset.filter(method__in=self.methods)
        if filters['account']:
            queryset = queryset.filter(account_id__in=filters['account'])

        return queryset

    def get_transactions(self, filters):
        queryset = self.get_base_transactions(filters)

        queryset = queryset.filter(datetime__date__gte=filters['start'], datetime__date__lte=filters['end'])

        if filters['category']:
            queryset = queryset.filter(category_id__in=filters['category'])

        return queryset

    def get_analytic_transactions(self, filters):
        """Recorte dos painéis: fora movimentação interna e ajuste, que só
        remanejam ou corrigem saldo e inflariam entrada e saída dos dois lados."""
        return self.get_transactions(filters).filter(nature=Nature.REGULAR)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filters'] = self.get_filters()
        context['accounts'] = Account.objects.all()
        context['categories'] = Category.objects.all()
        return context


class OwnedListView(LoginRequiredMixin, ListView):
    """Listagem somente leitura, restrita aos registros do usuário logado."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Mantém os demais parâmetros da URL ao trocar de página.
        params = self.request.GET.copy()
        params.pop('page', None)
        context['querystring'] = params.urlencode()

        return context


class ModalWriteMixin(LoginRequiredMixin):
    """Base das telas de escrita, todas servidas pelos modais da listagem.

    Não há template de formulário próprio: o GET volta para a lista, o POST
    inválido devolve os erros como mensagens e o sucesso retorna à página que o
    usuário estava vendo.
    """

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    # Listagem que serve os modais desta view, e para onde o GET e o sucesso
    # voltam quando não há um 'back' aproveitável.
    list_route = 'app:transactions_list'

    def get(self, request, *args, **kwargs):
        # Estas rotas existem só para receber o POST dos modais; não há template
        # de formulário próprio. Um GET (link colado, F5, histórico) volta para
        # a lista em vez de estourar TemplateDoesNotExist.
        return redirect(self.list_route)

    def get_success_url(self):
        # Devolve o usuário para a listagem com os filtros e a página que ele
        # estava vendo, em vez de jogá-lo no topo da lista sem filtro.
        # A validação é a do próprio Django: um simples startswith('/') deixaria
        # passar '//evil.com', que o navegador lê como protocol-relative.
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


class RevisionCreateMixin:
    """Criação com trilha de auditoria, a mesma do admin (VersionAdmin).

    Sem o create_revision o histórico ficaria cego para o que sai destas telas.
    Cada view informa o rótulo do que criou, usado na mensagem e no comentário
    da revisão.
    """

    success_message = None
    revision_comment = 'Criado pela tela de transações.'

    def form_valid(self, form):
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment(self.revision_comment)
            response = super().form_valid(form)

        messages.success(self.request, self.success_message)
        return response


class DerivedProtectedMixin:
    """Bloqueia a edição de transações geradas por um registro de origem: os
    valores delas derivam do parcelamento, da transferência ou do investimento,
    e mexer numa perna isolada deixaria o conjunto inconsistente.

    A exclusão não passa por aqui: ela apaga o registro de origem inteiro,
    que é o gesto coerente com o que o usuário vê na tela.
    """

    def get_object(self, queryset=None):
        transaction = super().get_object(queryset)
        if transaction.is_derived:
            raise PermissionDenied('Transações de parcelamento, transferência ou investimento são editadas pelo registro de origem, no portal de administração.')
        return transaction
