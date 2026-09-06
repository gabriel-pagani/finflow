"""Assinaturas: o cadastro que vira cobrança sozinho, mês após mês.

Três coisas precisam ficar presas aqui. A cobrança sai uma vez por competência —
nem duas, se a geração rodar de novo, nem nenhuma, se ninguém abrir o sistema
por uma semana. Ela cai na fatura do cartão, como toda compra no crédito. E a
assinatura, quando é removida, leva só a si mesma: o que já foi cobrado é
dinheiro que saiu, e continua no extrato depois do cancelamento.

O dia de hoje é sempre informado nos testes de geração. Ele é a única entrada
que decide o que vence, e deixá-lo ao relógio faria a suíte passar ou falhar
conforme o dia do mês em que fosse rodada.
"""

from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
import pytest

from app.forms import SubscriptionForm, month_choices
from app.models import Card, Method, Recurrence, Subscription, Transaction, Type, add_months


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice_subscription(alice, make_subscription):
    """Assinatura da Alice que começa em janeiro e cobra dia 10.

    O cartão fecha dia 20 e vence dia 27: a cobrança do dia 10 entra na fatura
    do próprio mês, que vence no 27 dele.
    """
    return make_subscription(alice, start=date(2026, 1, 1), charge_day=10)


def subscription_payload(account, card, category, **overrides):
    data = {
        'description': 'Netflix',
        'value': '55.90',
        'charge_day': '1',
        'recurrence': 'MONTHLY',
        'start': timezone.localdate().replace(day=1).isoformat(),
        'account': account.pk,
        'card': card.pk,
        'category': category.pk,
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------
# Geração das cobranças
# --------------------------------------------------------------------------

class TestGeracao:
    def test_nao_cobra_antes_do_dia(self, alice_subscription):
        assert alice_subscription.generate_charges(today=date(2026, 1, 9)) == []
        assert Transaction.objects.count() == 0

    def test_cobra_no_dia(self, alice_subscription):
        created = alice_subscription.generate_charges(today=date(2026, 1, 10))

        assert len(created) == 1
        transaction = created[0]
        assert transaction.reference == date(2026, 1, 1)
        assert transaction.value == Decimal('21.90')
        assert (transaction.type, transaction.method) == (Type.OUT, Method.CREDIT)
        assert transaction.description == 'Spotify'

    def test_cobranca_cai_no_vencimento_da_fatura(self, alice_subscription):
        transaction = alice_subscription.generate_charges(today=date(2026, 1, 10))[0]

        # Cobrou dia 10, antes do fechamento (20): entra na fatura de janeiro,
        # que vence no dia 27 — uma terça-feira, sem empurrão de fim de semana.
        assert transaction.datetime.date() == date(2026, 1, 27)

    def test_a_mesma_competencia_nao_sai_duas_vezes(self, alice_subscription):
        alice_subscription.generate_charges(today=date(2026, 1, 10))

        assert alice_subscription.generate_charges(today=date(2026, 1, 20)) == []
        assert Transaction.objects.count() == 1

    def test_recupera_os_meses_que_ficaram_para_tras(self, alice_subscription):
        """Uma semana sem rodar não perde mês: a execução seguinte lança todos.

        E cada um vai para a fatura em que teria caído, não todos para a de
        hoje — senão três meses parados virariam um pico numa fatura só.
        """
        created = alice_subscription.generate_charges(today=date(2026, 3, 15))

        assert [transaction.reference for transaction in created] == [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
        ]
        assert [transaction.datetime.date() for transaction in created] == [
            date(2026, 1, 27), date(2026, 2, 27), date(2026, 3, 27),
        ]

    def test_o_mes_corrente_espera_o_dia_chegar(self, alice_subscription):
        """Março ainda não cobrou no dia 5: só janeiro e fevereiro vencem."""
        created = alice_subscription.generate_charges(today=date(2026, 3, 5))

        assert [transaction.reference for transaction in created] == [date(2026, 1, 1), date(2026, 2, 1)]
        assert alice_subscription.last_reference == date(2026, 2, 1)

    def test_assinatura_nova_comeca_no_mes_do_cadastro(self, alice, make_subscription):
        """Sem retroagir: quem cadastra hoje não ganha histórico inventado."""
        subscription = make_subscription(alice)

        assert subscription.start == timezone.localdate().replace(day=1)
        assert subscription.last_reference is None

    def test_dia_31_cobra_no_ultimo_dia_do_mes_curto(self, alice, make_subscription):
        """Fevereiro não tem 31: a cobrança encosta no fim do mês, não escorrega."""
        subscription = make_subscription(alice, start=date(2026, 2, 1), charge_day=31)

        transaction = subscription.generate_charges(today=date(2026, 2, 28))[0]

        assert subscription.charge_date(date(2026, 2, 1)) == date(2026, 2, 28)
        # Cobrou dia 28, depois do fechamento (20): cai na fatura de março.
        assert transaction.datetime.date() == date(2026, 3, 27)

    def test_geracao_em_lote_alcanca_todos_os_usuarios(self, alice, bob, make_subscription):
        make_subscription(alice, start=date(2026, 1, 1), charge_day=10)
        make_subscription(bob, start=date(2026, 1, 1), charge_day=10)

        assert Subscription.generate_due(today=date(2026, 1, 10)) == 2
        assert Transaction.objects.filter(user=alice).count() == 1
        assert Transaction.objects.filter(user=bob).count() == 1

    def test_geracao_por_usuario_nao_toca_a_do_outro(self, alice, bob, make_subscription):
        make_subscription(alice, start=date(2026, 1, 1), charge_day=10)
        make_subscription(bob, start=date(2026, 1, 1), charge_day=10)

        assert Subscription.generate_due(user=alice, today=date(2026, 1, 10)) == 1
        assert Transaction.objects.filter(user=bob).count() == 0


# --------------------------------------------------------------------------
# Recorrência
# --------------------------------------------------------------------------

class TestRecorrencia:
    """Nem toda assinatura é mensal, e o que muda entre elas é só o passo.

    A competência continua sendo o mês: a anual salta doze de uma vez, a
    trimestral três. O ponto de partida é sempre a primeira competência, e não o
    calendário — assinatura anual cadastrada em março cobra em março.
    """

    @pytest.mark.parametrize(('recurrence', 'expected'), [
        (Recurrence.MONTHLY, 12),
        (Recurrence.BIMONTHLY, 6),
        (Recurrence.QUARTERLY, 4),
        (Recurrence.SEMIANNUAL, 2),
        (Recurrence.ANNUAL, 1),
    ])
    def test_quantas_cobrancas_saem_num_ano(self, recurrence, expected, alice, make_subscription):
        subscription = make_subscription(alice, start=date(2026, 1, 1), charge_day=10, recurrence=recurrence)

        assert len(subscription.generate_charges(today=date(2026, 12, 31))) == expected

    def test_trimestral_salta_de_tres_em_tres(self, alice, make_subscription):
        subscription = make_subscription(alice, start=date(2026, 1, 1), charge_day=10, recurrence=Recurrence.QUARTERLY)

        created = subscription.generate_charges(today=date(2026, 8, 1))

        assert [transaction.reference for transaction in created] == [
            date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1),
        ]

    def test_anual_espera_o_ano_virar(self, alice, make_subscription):
        subscription = make_subscription(alice, start=date(2026, 3, 1), charge_day=10, recurrence=Recurrence.ANNUAL)
        subscription.generate_charges(today=date(2026, 3, 10))

        # O ano inteiro passa sem nada a lançar, mesmo com a tela sendo aberta
        # todo dia: a próxima competência é março do ano seguinte.
        assert subscription.generate_charges(today=date(2027, 2, 28)) == []

        seguinte = subscription.generate_charges(today=date(2027, 3, 10))
        assert [transaction.reference for transaction in seguinte] == [date(2027, 3, 1)]

    def test_a_recorrencia_padrao_e_mensal(self, alice, make_subscription):
        assert make_subscription(alice).recurrence == Recurrence.MONTHLY

    def test_cadastro_pela_tela_aceita_a_recorrencia_escolhida(self, alice_logged, alice, account, category, make_card):
        card = make_card(alice)

        alice_logged.post(
            reverse('app:subscription_create'),
            subscription_payload(account, card, category, recurrence=Recurrence.ANNUAL),
        )

        assert Subscription.objects.get().recurrence == Recurrence.ANNUAL

    def test_nada_a_gerar_nao_abre_transacao(self, alice, make_subscription, django_assert_num_queries):
        """A saída barata: sem cobrança vencida, a geração não trava linha.

        Este caminho roda a cada página aberta, e uma anual passa onze meses do
        ano sem nada a fazer — o custo dela precisa ser a consulta que já
        aconteceu, e mais nada.
        """
        subscription = make_subscription(alice, start=date(2026, 1, 1), charge_day=10)

        with django_assert_num_queries(0):
            assert subscription.generate_charges(today=date(2026, 1, 9)) == []


# --------------------------------------------------------------------------
# A cobrança depois de lançada
# --------------------------------------------------------------------------

class TestCobrancaLancada:
    def test_remover_a_assinatura_mantem_as_cobrancas(self, alice_subscription):
        alice_subscription.generate_charges(today=date(2026, 3, 15))

        alice_subscription.delete()

        transactions = Transaction.objects.all()
        assert transactions.count() == 3
        # O vínculo some; a competência fica, dizendo de que mês era cada uma.
        assert all(transaction.subscription_id is None for transaction in transactions)
        assert transactions.filter(reference=date(2026, 2, 1)).exists()

    def test_a_cobranca_e_editavel_como_qualquer_transacao(self, alice_subscription):
        """A assinatura lança e solta: o mês que veio com outro preço se corrige
        na própria linha, sem precisar mexer no cadastro."""
        transaction = alice_subscription.generate_charges(today=date(2026, 1, 10))[0]

        assert transaction.is_derived is False
        assert transaction.is_deletable is True

    def test_apagar_uma_cobranca_nao_a_faz_voltar(self, alice_subscription):
        """O que já foi gerado não se refaz: a competência ficou marcada."""
        transaction = alice_subscription.generate_charges(today=date(2026, 1, 10))[0]
        transaction.delete()

        assert alice_subscription.generate_charges(today=date(2026, 1, 25)) == []
        assert Transaction.objects.count() == 0


# --------------------------------------------------------------------------
# Cadastro pela tela
# --------------------------------------------------------------------------

class TestCadastro:
    def test_usuario_cadastra_a_propria_assinatura(self, alice_logged, alice, account, category, make_card):
        card = make_card(alice)

        response = alice_logged.post(
            reverse('app:subscription_create'),
            subscription_payload(account, card, category),
        )

        assert response.status_code == 302
        subscription = Subscription.objects.get()
        assert subscription.user == alice
        assert (subscription.description, subscription.value, subscription.charge_day) == ('Netflix', Decimal('55.90'), 1)

    def test_cadastro_ja_lanca_a_cobranca_do_mes(self, alice_logged, alice, account, category, make_card):
        """Dia 1º já passou em qualquer dia do mês: a cobrança sai no cadastro."""
        card = make_card(alice)

        alice_logged.post(reverse('app:subscription_create'), subscription_payload(account, card, category))

        subscription = Subscription.objects.get()
        assert subscription.transactions.count() == 1
        assert subscription.last_reference == subscription.start

    def test_cartao_e_obrigatorio(self, alice_logged, alice, account, category, make_card):
        card = make_card(alice)

        payload = subscription_payload(account, card, category)
        payload['card'] = ''

        response = alice_logged.post(reverse('app:subscription_create'), payload)

        assert response.status_code == 302
        assert Subscription.objects.count() == 0

    def test_cartao_de_outro_usuario_e_recusado(self, alice, bob, account, category, make_card):
        form = SubscriptionForm(
            data=subscription_payload(account, make_card(bob), category),
            user=alice,
        )

        assert not form.is_valid()
        assert 'card' in form.errors

    def test_listagem_mostra_so_as_assinaturas_do_dono(self, alice_logged, alice, bob, make_subscription):
        make_subscription(alice, description='Spotify da Alice')
        make_subscription(bob, description='Netflix do Bob')

        response = alice_logged.get(reverse('app:subscriptions_list'))

        assert response.status_code == 200
        assert list(response.context['object_list']) == list(Subscription.objects.filter(user=alice))

    def test_ninguem_edita_a_assinatura_do_outro(self, alice_logged, bob, account, category, make_subscription):
        subscription = make_subscription(bob)

        response = alice_logged.post(
            reverse('app:subscription_update', args=[subscription.pk]),
            subscription_payload(account, subscription.card, category),
        )

        assert response.status_code == 404
        assert Subscription.objects.get(pk=subscription.pk).description == 'Spotify'

    def test_ninguem_remove_a_assinatura_do_outro(self, alice_logged, bob, make_subscription):
        subscription = make_subscription(bob)

        response = alice_logged.post(reverse('app:subscription_delete', args=[subscription.pk]))

        assert response.status_code == 404
        assert Subscription.objects.filter(pk=subscription.pk).exists()

    def test_remocao_pela_tela_preserva_as_cobrancas(self, alice_logged, alice, make_subscription):
        subscription = make_subscription(alice, start=date(2026, 1, 1))
        subscription.generate_charges(today=date(2026, 1, 10))

        response = alice_logged.post(reverse('app:subscription_delete', args=[subscription.pk]))

        assert response.status_code == 302
        assert not Subscription.objects.filter(pk=subscription.pk).exists()
        assert Transaction.objects.filter(user=alice).count() == 1


# --------------------------------------------------------------------------
# Primeira cobrança
# --------------------------------------------------------------------------

class TestPrimeiraCobranca:
    """A âncora da assinatura, escolhida no cadastro.

    Serve para o que já vinha sendo cobrado antes de existir cadastro: a anual
    paga em janeiro cobra em janeiro, e não no mês em que alguém se lembrou de
    registrá-la.
    """

    def test_opcoes_cobrem_os_doze_ultimos_meses(self):
        choices = month_choices()
        current = timezone.localdate().replace(day=1)

        assert len(choices) == 12
        assert choices[0][0] == current.isoformat()
        # O último é onze meses atrás: um ano fechado, contando o corrente.
        assert choices[-1][0] == add_months(current, -11).isoformat()

    def test_mes_antigo_da_assinatura_entra_na_lista(self, alice, make_subscription):
        """Assinatura ancorada fora da janela continua editável sem se alterar."""
        antiga = add_months(timezone.localdate().replace(day=1), -30)

        choices = month_choices(antiga)

        assert len(choices) == 13
        assert choices[-1][0] == antiga.isoformat()

    def test_mes_passado_ancora_sem_lancar_nada(self, alice_logged, alice, account, category, make_card):
        """O caso que motivou o campo: anual paga em janeiro, cadastrada hoje.

        Janeiro foi pago por fora, então nada é lançado agora — o mês só diz de
        onde a contagem parte, e a próxima cobrança é a de janeiro que vem.
        """
        card = make_card(alice)
        janeiro = timezone.localdate().replace(month=1, day=1)

        alice_logged.post(
            reverse('app:subscription_create'),
            subscription_payload(account, card, category, recurrence=Recurrence.ANNUAL, start=janeiro.isoformat()),
        )

        subscription = Subscription.objects.get()
        assert subscription.start == janeiro
        assert subscription.transactions.count() == 0
        assert subscription.next_reference == add_months(janeiro, 12)

    def test_mensal_ancorada_no_passado_cobra_so_o_mes_corrente(self, alice_logged, alice, account, category, make_card):
        """O passado fica de fora; o mês corrente continua sendo do sistema."""
        card = make_card(alice)
        janeiro = timezone.localdate().replace(month=1, day=1)
        corrente = timezone.localdate().replace(day=1)

        alice_logged.post(
            reverse('app:subscription_create'),
            subscription_payload(account, card, category, start=janeiro.isoformat()),
        )

        subscription = Subscription.objects.get()
        # charge_day 1 já passou em qualquer dia do mês: sai a do mês corrente.
        assert [transaction.reference for transaction in subscription.transactions.all()] == [corrente]

    def test_ancora_marca_a_ultima_competencia_antes_do_mes_corrente(self, alice, make_subscription):
        """A conta da âncora, mês a mês, sem depender do relógio."""
        subscription = make_subscription(alice, start=date(2026, 1, 1), recurrence=Recurrence.QUARTERLY)

        subscription.anchor_past(today=date(2026, 9, 5))

        # Competências: jan, abr, jul, out. A última antes de setembro é julho.
        assert subscription.last_reference == date(2026, 7, 1)
        assert subscription.next_reference == date(2026, 10, 1)

    def test_ancora_no_mes_corrente_nao_marca_nada(self, alice, make_subscription):
        subscription = make_subscription(alice, start=date(2026, 9, 1))

        subscription.anchor_past(today=date(2026, 9, 5))

        assert subscription.last_reference is None
        assert subscription.next_reference == date(2026, 9, 1)

    def test_mes_fora_da_lista_e_recusado(self, alice, account, category, make_card):
        futuro = add_months(timezone.localdate().replace(day=1), 1)

        form = SubscriptionForm(
            data=subscription_payload(account, make_card(alice), category, start=futuro.isoformat()),
            user=alice,
        )

        assert not form.is_valid()
        assert 'start' in form.errors

    def test_primeira_cobranca_nao_muda_depois_de_cobrar(self, alice, account, category, make_subscription):
        subscription = make_subscription(alice, charge_day=1)
        subscription.generate_charges()

        form = SubscriptionForm(
            data=subscription_payload(account, subscription.card, category, charge_day='1', start=add_months(subscription.start, -1).isoformat()),
            user=alice,
            instance=subscription,
        )

        assert not form.is_valid()
        assert 'start' in form.errors

    def test_edicao_mantendo_o_mesmo_mes_passa(self, alice, account, category, make_subscription):
        subscription = make_subscription(alice, charge_day=1)
        subscription.generate_charges()

        form = SubscriptionForm(
            data=subscription_payload(account, subscription.card, category, charge_day='1', start=subscription.start.isoformat()),
            user=alice,
            instance=subscription,
        )

        assert form.is_valid(), form.errors


# --------------------------------------------------------------------------
# As telas geram o que o cron não gerou
# --------------------------------------------------------------------------

class TestGeracaoPelaTela:
    @pytest.mark.parametrize('route', ['app:overview', 'app:forecast', 'app:transactions_list', 'app:subscriptions_list'])
    def test_abrir_a_tela_lanca_o_que_venceu(self, route, alice_logged, alice, make_subscription):
        """Cron parado não some com a cobrança: quem abre o sistema a encontra.

        A assinatura começa no mês passado e cobra no dia 1º, então há sempre ao
        menos uma competência vencida quando a página é aberta.
        """
        make_subscription(alice, start=date(2026, 1, 1), charge_day=1)

        response = alice_logged.get(reverse(route))

        assert response.status_code == 200
        assert Transaction.objects.filter(user=alice).exists()

    def test_a_tela_do_outro_usuario_nao_gera_nada(self, bob_logged, alice, make_subscription):
        make_subscription(alice, start=date(2026, 1, 1), charge_day=1)

        bob_logged.get(reverse('app:overview'))

        assert Transaction.objects.filter(user=alice).count() == 0


# --------------------------------------------------------------------------
# Cartão preso a uma assinatura
# --------------------------------------------------------------------------

class TestCartaoEmUso:
    def test_cartao_com_assinatura_nao_e_removido(self, alice_logged, alice, make_subscription):
        subscription = make_subscription(alice)

        response = alice_logged.post(reverse('app:card_delete', args=[subscription.card.pk]))

        assert response.status_code == 302
        assert Card.objects.filter(pk=subscription.card.pk).exists()
