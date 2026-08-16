"""Isolamento entre usuários nas telas de transação.

O que se prova aqui é que um usuário não alcança os dados de outro. As telas
filtram por request.user, e nenhuma varredura externa cobre isso: o site é todo
autenticado, então um scanner anônimo nunca chega a estas rotas. Sem estes
testes, remover um filtro de queryset passaria despercebido.
"""

from django.urls import reverse
import pytest

from app.models import Transaction


pytestmark = pytest.mark.django_db


def login(client, user, password):
    assert client.login(username=user.username, password=password)


@pytest.fixture
def alice_logged(client, alice):
    login(client, alice, 'senha-de-teste-alice')
    return client


@pytest.fixture
def bob_logged(client, bob):
    login(client, bob, 'senha-de-teste-bob')
    return client


class TestAnonimoNaoAcessa:
    """Sem sessão, tudo redireciona para o login."""

    @pytest.mark.parametrize('name', ['overview', 'forecast', 'transactions_list'])
    def test_paginas_exigem_login(self, client, name):
        response = client.get(reverse(f'app:{name}'))

        assert response.status_code == 302
        assert reverse('app:login') in response['Location']

    def test_escrita_exige_login(self, client, alice, make_transaction):
        transaction = make_transaction(alice)

        response = client.post(
            reverse('app:transaction_update', args=[transaction.pk]),
            {'description': 'invadido'},
        )

        assert response.status_code == 302
        assert reverse('app:login') in response['Location']
        transaction.refresh_from_db()
        assert transaction.description == 'Almoço'


class TestListagemIsolada:
    """A listagem mostra só o que é do próprio usuário."""

    def test_nao_lista_transacao_alheia(self, bob_logged, alice, bob, make_transaction):
        make_transaction(alice, description='Segredo da Alice')
        make_transaction(bob, description='Compra do Bob')

        response = bob_logged.get(reverse('app:transactions_list'))

        assert response.status_code == 200
        listadas = response.context['object_list']
        assert [t.description for t in listadas] == ['Compra do Bob']

    def test_totais_ignoram_transacao_alheia(self, bob_logged, alice, bob, make_transaction):
        make_transaction(alice, value='500.00')
        make_transaction(bob, value='25.00')

        response = bob_logged.get(reverse('app:transactions_list'))

        assert response.context['total_outcome'] == 25.00
        assert response.context['total_count'] == 1

    def test_filtro_de_busca_nao_vaza(self, bob_logged, alice, make_transaction):
        make_transaction(alice, description='Almoço da Alice')

        response = bob_logged.get(reverse('app:transactions_list'), {'search': 'Alice'})

        assert response.context['object_list'].count() == 0


class TestEscritaIsolada:
    """Editar ou apagar transação de outro usuário não é possível."""

    def test_update_de_transacao_alheia_da_404(self, bob_logged, alice, make_transaction):
        transaction = make_transaction(alice)

        response = bob_logged.post(
            reverse('app:transaction_update', args=[transaction.pk]),
            {
                'account': transaction.account_id,
                'category': transaction.category_id,
                'type': transaction.type,
                'method': transaction.method,
                'description': 'invadido',
                'value': '1.00',
                'datetime': '2026-01-01T10:00',
            },
        )

        assert response.status_code == 404
        transaction.refresh_from_db()
        assert transaction.description == 'Almoço'

    def test_delete_de_transacao_alheia_da_404(self, bob_logged, alice, make_transaction):
        transaction = make_transaction(alice)

        response = bob_logged.post(reverse('app:transaction_delete', args=[transaction.pk]))

        assert response.status_code == 404
        assert Transaction.objects.filter(pk=transaction.pk).exists()

    def test_create_ignora_user_enviado_no_post(self, bob_logged, alice, bob, account, category, business_rule):
        """O dono vem da sessão: mandar 'user' no POST não muda a titularidade."""
        response = bob_logged.post(
            reverse('app:transaction_create'),
            {
                'user': alice.pk,
                'account': account.pk,
                'category': category.pk,
                'type': 'OUT',
                'method': 'DEBIT',
                'description': 'Tentativa',
                'value': '10.00',
                'datetime': '2026-01-01T10:00',
            },
        )

        assert response.status_code == 302
        created = Transaction.objects.get(description='Tentativa')
        assert created.user_id == bob.pk


class TestPaineisIsolados:
    """Os cards e gráficos agregam apenas o próprio movimento."""

    def test_overview_nao_soma_transacao_alheia(self, bob_logged, alice, bob, make_transaction):
        make_transaction(alice, value='500.00')
        make_transaction(bob, value='25.00')

        response = bob_logged.get(reverse('app:overview'))

        assert response.context['cards']['outcome'] == 25.00

    def test_overview_de_usuario_sem_dados_fica_zerado(self, bob_logged, alice, make_transaction):
        make_transaction(alice, value='500.00')

        response = bob_logged.get(reverse('app:overview'))

        cards = response.context['cards']
        assert cards['outcome'] == 0.0
        assert cards['balance'] == 0.0
        assert response.context['chart_categories'] == []


class TestTransacaoDerivada:
    """Parcelamento e investimento se editam pelo registro de origem."""

    def test_dono_nao_edita_transacao_derivada(self, alice_logged, derived_transaction):
        response = alice_logged.post(
            reverse('app:transaction_update', args=[derived_transaction.pk]),
            {
                'account': derived_transaction.account_id,
                'category': derived_transaction.category_id,
                'type': derived_transaction.type,
                'method': derived_transaction.method,
                'description': 'alterado',
                'value': '1.00',
                'datetime': '2026-01-01T10:00',
            },
        )

        assert response.status_code == 403

    def test_dono_nao_apaga_transacao_derivada(self, alice_logged, derived_transaction):
        response = alice_logged.post(
            reverse('app:transaction_delete', args=[derived_transaction.pk]),
        )

        assert response.status_code == 403
        assert Transaction.objects.filter(pk=derived_transaction.pk).exists()


class TestRedirectSeguro:
    """O 'back' do formulário não leva para fora do site."""

    @pytest.mark.parametrize('destino', [
        'https://evil.com/phish',
        '//evil.com',
        'http://evil.com',
    ])
    def test_back_externo_e_descartado(self, alice_logged, account, category, business_rule, destino):
        response = alice_logged.post(
            reverse('app:transaction_create'),
            {
                'back': destino,
                'account': account.pk,
                'category': category.pk,
                'type': 'OUT',
                'method': 'DEBIT',
                'description': 'Teste',
                'value': '10.00',
                'datetime': '2026-01-01T10:00',
            },
        )

        assert response.status_code == 302
        assert response['Location'] == reverse('app:transactions_list')

    def test_back_interno_e_preservado(self, alice_logged, account, category, business_rule):
        interno = f"{reverse('app:transactions_list')}?page=2"

        response = alice_logged.post(
            reverse('app:transaction_create'),
            {
                'back': interno,
                'account': account.pk,
                'category': category.pk,
                'type': 'OUT',
                'method': 'DEBIT',
                'description': 'Teste',
                'value': '10.00',
                'datetime': '2026-01-01T10:00',
            },
        )

        assert response['Location'] == interno
