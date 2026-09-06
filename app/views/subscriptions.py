"""As telas da assinatura: o molde da cobrança que se repete.

O que aparece nas transações é a cobrança que o cadastro gerou, e ela vive por
conta própria a partir daí — editar ou apagar a assinatura não a alcança.
"""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic.edit import CreateView, DeleteView, UpdateView
import reversion

from ..forms import SubscriptionForm
from ..models import Subscription
from .mixins import ModalWriteMixin, OwnedListView, RevisionCreateMixin, SubscriptionSyncMixin


class SubscriptionsListView(SubscriptionSyncMixin, OwnedListView):
    """Assinaturas do usuário logado, com criação e edição pelos modais da página.

    Mesmo lugar que o cartão ocupa: é cadastro, não lançamento. O que aparece
    nas transações é a cobrança que o cadastro gerou, e ela vive por conta
    própria a partir daí.
    """

    model = Subscription
    template_name = 'app/subscriptions_list.html'
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().select_related('account', 'card', 'card__account', 'category')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = SubscriptionForm(user=self.request.user)
        return context


class SubscriptionWriteMixin(ModalWriteMixin):
    """Escrita das assinaturas do próprio usuário."""

    model = Subscription
    form_class = SubscriptionForm
    list_route = 'app:subscriptions_list'

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)


class SubscriptionCreateView(RevisionCreateMixin, SubscriptionWriteMixin, CreateView):
    """Cadastra a assinatura e já lança a cobrança do mês, se o dia dela passou.

    A geração é feita aqui, e não deixada para o cron ou para a próxima tela,
    porque quem acabou de cadastrar espera ver a cobrança na lista. Se o dia
    ainda não chegou, nada sai agora — a cobrança é do dia dela, não do
    cadastro. O que ficou para trás do mês corrente não sai nunca: quem cuida
    disso é a âncora, no formulário.

    Quando nada é lançado, a mensagem diz quando será: sem ela, cadastrar uma
    anual de janeiro pareceria não ter feito nada.
    """

    success_message = 'Assinatura cadastrada com sucesso.'
    revision_comment = 'Criado pela tela de assinaturas.'

    def form_valid(self, form):
        response = super().form_valid(form)

        if self.object.generate_charges():
            messages.success(self.request, f'Cobrança lançada no vencimento da fatura do cartão {self.object.card}.')
        else:
            messages.info(self.request, f'A próxima cobrança será lançada no dia {self.object.charge_day} de {self.object.next_reference():%m/%Y}.')

        return response


class SubscriptionUpdateView(SubscriptionWriteMixin, UpdateView):
    """Edita uma assinatura do usuário logado.

    Valor e dia novos valem das próximas cobranças em diante. As que já saíram
    ficam como estão: elas guardam o que foi cobrado de fato, e reescrevê-las
    apagaria o registro de um preço que mudou.
    """

    def form_valid(self, form):
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment('Editado pela tela de assinaturas.')
            response = super().form_valid(form)

        messages.success(self.request, 'Assinatura atualizada com sucesso. Os dados novos valem para as próximas cobranças; as já lançadas mantêm o valor e a data que tinham.')
        return response


class SubscriptionDeleteView(SubscriptionWriteMixin, DeleteView):
    """Apaga uma assinatura do usuário logado, e só ela.

    As cobranças já lançadas ficam: elas são dinheiro que saiu, e cancelar o
    serviço não desfaz os meses pagos. O vínculo delas é SET_NULL, então o que
    some é a ligação — as transações continuam na lista, editáveis como
    qualquer outra.
    """

    # Como nos demais DeleteView: a confirmação só precisa do POST, e herdar o
    # SubscriptionForm faria validar campos que ela nem envia.
    form_class = forms.Form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('user', None)
        kwargs.pop('instance', None)
        return kwargs

    def form_valid(self, form):
        with reversion.create_revision():
            reversion.set_user(self.request.user)
            reversion.set_comment('Removido pela tela de assinaturas.')
            reversion.add_to_revision(self.object)

        self.object.delete()

        messages.success(self.request, 'Assinatura removida com sucesso. As cobranças já lançadas continuam nas transações.')
        return redirect(self.get_success_url())
