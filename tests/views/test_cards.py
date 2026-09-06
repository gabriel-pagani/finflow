"""Cadastro de cartões: quem pode criar, ver, editar e apagar cada um.

O cartão é do usuário, não da conta — a conta é cadastro global, compartilhado.
Dois usuários podem ter cartões com o mesmo final na mesma conta, e nenhum dos
dois alcança o do outro. É essa fronteira que os casos abaixo prendem.

A outra metade do arquivo é a obrigatoriedade: no crédito o cartão passou a ser
exigido, e fora dele o campo nem é oferecido. As duas pontas importam — exigir
onde cabe, e não recusar um lançamento por um campo que a tela não mostrou.
"""

from decimal import Decimal

from django.urls import reverse
import pytest

from app.forms import InstallmentForm, TransactionForm
from app.models import Card, Installment, Method, Transaction, Type


pytestmark = pytest.mark.django_db


@pytest.fixture
def alice_card(alice, account, business_rules):
    return Card.objects.create(user=alice, account=account, last_digits='1234', closing_day=20, due_day=27)


@pytest.fixture
def bob_card(bob, account, business_rules):
    return Card.objects.create(user=bob, account=account, last_digits='5678', closing_day=10, due_day=20)


def card_payload(account, **overrides):
    data = {
        'account': account.pk,
        'last_digits': '9999',
        'closing_day': '15',
        'due_day': '25',
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------
# Cadastro pelo próprio usuário
# --------------------------------------------------------------------------

class TestCadastro:
    def test_usuario_cadastra_o_proprio_cartao(self, alice_logged, alice, account, business_rules):
        response = alice_logged.post(reverse('app:card_create'), card_payload(account))

        assert response.status_code == 302
        card = Card.objects.get()
        assert card.user == alice
        assert (card.last_digits, card.closing_day, card.due_day) == ('9999', 15, 25)

    def test_listagem_mostra_so_os_cartoes_do_dono(self, alice_logged, alice_card, bob_card):
        response = alice_logged.get(reverse('app:cards_list'))

        assert response.status_code == 200
        assert [c.pk for c in response.context['object_list']] == [alice_card.pk]
        assert '5678' not in response.content.decode()

    def test_mesmo_final_na_mesma_conta_para_donos_diferentes(self, bob_logged, alice_card, account):
        """A unicidade conta o dono: o final do cartão da Alice não bloqueia o Bob."""
        response = bob_logged.post(reverse('app:card_create'), card_payload(account, last_digits='1234'))

        assert response.status_code == 302
        assert Card.objects.filter(last_digits='1234').count() == 2

    def test_final_repetido_do_mesmo_dono_na_mesma_conta_e_recusado(self, alice_logged, alice_card, account):
        response = alice_logged.post(reverse('app:card_create'), card_payload(account, last_digits='1234'), follow=True)

        assert Card.objects.filter(user=alice_card.user, last_digits='1234').count() == 1
        assert 'Você já tem um cartão com este final nesta conta.' in response.content.decode()

    def test_final_precisa_ter_quatro_digitos(self, alice_logged, account, business_rules):
        alice_logged.post(reverse('app:card_create'), card_payload(account, last_digits='12'))

        assert not Card.objects.exists()

    def test_dono_edita_o_proprio_cartao(self, alice_logged, alice_card):
        response = alice_logged.post(
            reverse('app:card_update', args=[alice_card.pk]),
            card_payload(alice_card.account, last_digits='1234', closing_day='5', due_day='15'),
        )

        assert response.status_code == 302
        alice_card.refresh_from_db()
        assert (alice_card.closing_day, alice_card.due_day) == (5, 15)

    def test_dono_apaga_cartao_sem_lancamento(self, alice_logged, alice_card):
        response = alice_logged.post(reverse('app:card_delete', args=[alice_card.pk]))

        assert response.status_code == 302
        assert not Card.objects.filter(pk=alice_card.pk).exists()

    def test_cartao_em_uso_nao_e_apagado(self, alice_logged, alice, alice_card, category):
        """PROTECT no banco, mas a tela recusa antes, com a razão em português."""
        Transaction.objects.create(
            user=alice, account=alice_card.account, card=alice_card, type=Type.OUT,
            method=Method.CREDIT, category=category, value=Decimal('10.00'),
            datetime='2026-05-27T14:30:00-03:00',
        )

        response = alice_logged.post(reverse('app:card_delete', args=[alice_card.pk]), follow=True)

        assert Card.objects.filter(pk=alice_card.pk).exists()
        assert 'não pode ser removido' in response.content.decode()

    def test_editar_cartao_nao_remexe_no_que_ja_foi_lancado(self, alice_logged, alice, alice_card, category):
        """A data já gravada é a que o usuário conferiu: o ciclo novo vale daqui para a frente."""
        transaction = Transaction.objects.create(
            user=alice, account=alice_card.account, card=alice_card, type=Type.OUT,
            method=Method.CREDIT, category=category, value=Decimal('10.00'),
            datetime='2026-05-27T14:30:00-03:00',
        )
        # Do banco, e não do que foi passado ao create: o campo entrou como
        # texto, e comparar com ele mediria a conversão, não a regra.
        transaction.refresh_from_db()
        antes = transaction.datetime

        alice_logged.post(
            reverse('app:card_update', args=[alice_card.pk]),
            card_payload(alice_card.account, last_digits='1234', closing_day='1', due_day='10'),
        )

        transaction.refresh_from_db()
        assert transaction.datetime == antes


# --------------------------------------------------------------------------
# Isolamento entre usuários
# --------------------------------------------------------------------------

class TestNaoAlcancaCartaoAlheio:
    def test_edicao_de_cartao_alheio_da_404(self, bob_logged, alice_card):
        response = bob_logged.post(
            reverse('app:card_update', args=[alice_card.pk]),
            card_payload(alice_card.account, closing_day='1', due_day='2'),
        )

        assert response.status_code == 404
        alice_card.refresh_from_db()
        assert (alice_card.closing_day, alice_card.due_day) == (20, 27)

    def test_exclusao_de_cartao_alheio_da_404(self, bob_logged, alice_card):
        response = bob_logged.post(reverse('app:card_delete', args=[alice_card.pk]))

        assert response.status_code == 404
        assert Card.objects.filter(pk=alice_card.pk).exists()

    @pytest.mark.parametrize('name', ['cards_list', 'card_create'])
    def test_rotas_de_cartao_exigem_login(self, client, name):
        response = client.post(reverse(f'app:{name}'))

        assert response.status_code == 302
        assert reverse('app:login') in response['Location']

    def test_formulario_so_oferece_os_cartoes_do_dono(self, alice, alice_card, bob_card):
        form = TransactionForm(user=alice)

        assert list(form.fields['card'].queryset) == [alice_card]

    def test_cartao_alheio_enviado_por_pk_e_recusado(self, bob, account, category, bob_card, alice_card):
        """O queryset do campo é a trava: a pk da Alice nem chega ao model."""
        form = TransactionForm(user=bob, data={
            'datetime': '2026-05-19T14:30',
            'account': account.pk,
            'card': alice_card.pk,
            'type': Type.OUT,
            'method': Method.CREDIT,
            'category': category.pk,
            'description': 'Mercado',
            'value': '150.00',
        })

        assert not form.is_valid()
        assert 'card' in form.errors


# --------------------------------------------------------------------------
# Obrigatoriedade: exigido no crédito, ausente fora dele
# --------------------------------------------------------------------------

class TestObrigatoriedade:
    def test_credito_sem_cartao_e_recusado(self, alice, alice_card, account, category):
        form = TransactionForm(user=alice, data={
            'datetime': '2026-05-19T14:30',
            'account': account.pk,
            'type': Type.OUT,
            'method': Method.CREDIT,
            'category': category.pk,
            'description': 'Mercado',
            'value': '150.00',
        })

        assert not form.is_valid()
        assert form.errors['card'] == [TransactionForm.REQUIRED_ERROR]

    def test_quem_nao_tem_cartao_recebe_o_caminho_do_cadastro(self, alice, account, category, business_rules):
        """A frase muda quando não há o que escolher: faltar cartão e não ter
        cartão nenhum pedem ações diferentes."""
        form = TransactionForm(user=alice, data={
            'datetime': '2026-05-19T14:30',
            'account': account.pk,
            'type': Type.OUT,
            'method': Method.CREDIT,
            'category': category.pk,
            'description': 'Mercado',
            'value': '150.00',
        })

        assert not form.is_valid()
        assert form.errors['card'] == [TransactionForm.NO_CARDS_ERROR]

    def test_fora_do_credito_o_cartao_e_descartado(self, alice, alice_card, account, category):
        """O campo não aparece no débito; um valor que sobrou de uma troca de
        método é ruído, e descartá-lo é melhor que recusar o lançamento."""
        form = TransactionForm(user=alice, data={
            'datetime': '2026-05-19T14:30',
            'account': account.pk,
            'card': alice_card.pk,
            'type': Type.OUT,
            'method': Method.DEBIT,
            'category': category.pk,
            'description': 'Mercado',
            'value': '150.00',
        })

        assert form.is_valid(), form.errors
        assert form.save().card_id is None

    def test_o_select_nao_oferece_opcao_vazia(self, alice, alice_card):
        """Sem 'sem cartão': onde o campo aparece, ele é obrigatório."""
        html = str(TransactionForm(user=alice)['card'])

        assert '---------' not in html
        assert 'Sem Cartão' not in html

    def test_parcelamento_exige_cartao(self, alice, alice_card, account, category):
        form = InstallmentForm(user=alice, data={
            'datetime': '2026-05-19T14:30',
            'account': account.pk,
            'category': category.pk,
            'description': 'Notebook',
            'value': '900.00',
            'installments': '3',
        })

        assert not form.is_valid()
        assert form.errors['card'] == [InstallmentForm.REQUIRED_ERROR]

    def test_parcelamento_com_cartao_passa(self, alice, alice_card, account, category):
        form = InstallmentForm(user=alice, data={
            'datetime': '2026-05-19T14:30',
            'account': account.pk,
            'card': alice_card.pk,
            'category': category.pk,
            'description': 'Notebook',
            'value': '900.00',
            'installments': '3',
        })

        assert form.is_valid(), form.errors
        assert form.save().transactions.count() == 3
