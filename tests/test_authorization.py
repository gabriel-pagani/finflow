"""Autorização: cada usuário alcança apenas os próprios dados.

Uma regra só é provada aqui, nas três formas em que ela pode ser quebrada — um
usuário não **vê**, não **edita** e não **apaga** o que pertence a outro. O que
o dono pode fazer com o que é dele fica no fim do arquivo, como contorno da
mesma fronteira.

Nenhuma varredura externa cobre isto: o site inteiro é autenticado, então um
scanner anônimo nunca chega às rotas que importam. É o filtro por request.user,
dentro de cada view, que sustenta a separação — e removê-lo passaria
despercebido sem estes testes.

Conta, categoria e regra de negócio são globais, sem dono. Vários casos abaixo
usam de propósito o mesmo cadastro para os dois usuários: o isolamento tem de
vir do filtro da view, não da sorte de cada um usar registros diferentes.
"""

from django.urls import reverse
import pytest

from app.models import Installment, Investment, Transaction, Transfer


pytestmark = pytest.mark.django_db


# Rotas de leitura: respondem a GET e renderizam dado do usuário.
READ_ROUTES = ['overview', 'forecast', 'transactions_list']

# Rotas de criação: existem só para receber o POST dos modais da listagem.
CREATE_ROUTES = ['transaction_create', 'installment_create', 'transfer_create']

# Rotas que agem sobre uma transação existente, identificada pela pk na URL.
OBJECT_ROUTES = ['transaction_update', 'transaction_delete']


def transaction_payload(transaction, **overrides):
    """POST válido de edição, copiado da própria transação.

    Só a descrição muda por padrão: se a autorização falhar, é nela que se vê o
    estrago, e os demais campos precisam estar presentes para o formulário
    chegar a validar.
    """
    data = {
        'account': transaction.account_id,
        'category': transaction.category_id,
        'type': transaction.type,
        'method': transaction.method,
        'description': 'invadido',
        'value': '1.00',
        'datetime': '2026-01-01T10:00',
    }
    data.update(overrides)
    return data


def assert_redirected_to_login(response):
    assert response.status_code == 302
    assert reverse('app:login') in response['Location']


class TestSemSessaoNadaAbre:
    """Sem sessão não se lê nem se escreve: toda rota volta para o login."""

    @pytest.mark.parametrize('name', READ_ROUTES)
    def test_leitura_exige_login(self, client, name):
        assert_redirected_to_login(client.get(reverse(f'app:{name}')))

    @pytest.mark.parametrize('name', CREATE_ROUTES)
    def test_criacao_exige_login(self, client, account, destination_account, category, business_rules, name):
        response = client.post(reverse(f'app:{name}'), {
            'account': account.pk,
            'origin': account.pk,
            'destination': destination_account.pk,
            'category': category.pk,
            'type': 'OUT',
            'method': 'DEBIT',
            'description': 'anonimo',
            'value': '10.00',
            'installments': 2,
            'datetime': '2026-01-01T10:00',
        })

        assert_redirected_to_login(response)
        assert not Transaction.objects.exists()
        assert not Installment.objects.exists()
        assert not Transfer.objects.exists()

    def test_edicao_exige_login(self, client, alice, make_transaction):
        transaction = make_transaction(alice)

        response = client.post(
            reverse('app:transaction_update', args=[transaction.pk]),
            transaction_payload(transaction),
        )

        assert_redirected_to_login(response)
        transaction.refresh_from_db()
        assert transaction.description == 'Almoco'

    def test_exclusao_exige_login(self, client, alice, make_transaction):
        transaction = make_transaction(alice)

        response = client.post(reverse('app:transaction_delete', args=[transaction.pk]))

        assert_redirected_to_login(response)
        assert Transaction.objects.filter(pk=transaction.pk).exists()

    @pytest.mark.parametrize('name', OBJECT_ROUTES)
    def test_login_vem_antes_de_procurar_o_objeto(self, client, name):
        """Nem a existência da pk é respondida ao anônimo.

        A checagem de sessão roda antes da busca, então pk inexistente devolve o
        mesmo redirect que pk real — sem 404 servindo de sonda.
        """
        assert_redirected_to_login(client.post(reverse(f'app:{name}', args=[99999])))


class TestNaoVeDadoAlheio:
    """Listagem e painéis mostram apenas o movimento do próprio usuário."""

    def test_listagem_nao_traz_transacao_alheia(self, bob_logged, alice, bob, make_transaction):
        make_transaction(alice, description='Segredo da Alice')
        make_transaction(bob, description='Compra do Bob')

        response = bob_logged.get(reverse('app:transactions_list'))

        assert response.status_code == 200
        assert [t.description for t in response.context['object_list']] == ['Compra do Bob']

    def test_pagina_renderizada_nao_cita_dado_alheio(self, bob_logged, alice, bob, make_transaction):
        """Além do queryset, o HTML entregue: um template que escapasse do
        object_list para outra fonte vazaria sem que a listagem acusasse."""
        make_transaction(alice, description='Segredo da Alice')
        make_transaction(bob, description='Compra do Bob')

        response = bob_logged.get(reverse('app:transactions_list'))

        assert 'Segredo da Alice' not in response.content.decode()

    def test_totais_ignoram_transacao_alheia(self, bob_logged, alice, bob, make_transaction):
        make_transaction(alice, value='500.00')
        make_transaction(bob, value='25.00')

        response = bob_logged.get(reverse('app:transactions_list'))

        assert response.context['total_outcome'] == 25.00
        assert response.context['total_count'] == 1

    def test_busca_por_descricao_nao_vaza(self, bob_logged, alice, make_transaction):
        make_transaction(alice, description='Almoco da Alice')

        response = bob_logged.get(reverse('app:transactions_list'), {'search': 'Alice'})

        assert response.context['object_list'].count() == 0

    def test_filtro_de_conta_compartilhada_nao_vaza(self, bob_logged, alice, bob, account, make_transaction):
        """A conta é a mesma para os dois: filtrar por ela não afrouxa o dono."""
        make_transaction(alice, description='Segredo da Alice')
        make_transaction(bob, description='Compra do Bob')

        response = bob_logged.get(reverse('app:transactions_list'), {'account': account.pk})

        assert [t.description for t in response.context['object_list']] == ['Compra do Bob']

    def test_filtro_de_categoria_compartilhada_nao_vaza(self, bob_logged, alice, bob, category, make_transaction):
        make_transaction(alice, description='Segredo da Alice')
        make_transaction(bob, description='Compra do Bob')

        response = bob_logged.get(reverse('app:transactions_list'), {'category': category.pk})

        assert [t.description for t in response.context['object_list']] == ['Compra do Bob']

    def test_periodo_amplo_nao_alcanca_o_outro(self, bob_logged, alice, make_transaction):
        make_transaction(alice, description='Segredo da Alice')

        response = bob_logged.get(reverse('app:transactions_list'), {
            'start': '2000-01-01',
            'end': '2100-12-31',
        })

        assert response.context['object_list'].count() == 0

    def test_paginacao_nao_alcanca_a_pagina_do_outro(self, bob_logged, alice, bob, make_transaction):
        """A lista pagina de 25 em 25: o volume da Alice não empurra páginas
        extras para o Bob, que tem uma transação só."""
        for i in range(26):
            make_transaction(alice, description=f'Alice {i}')
        make_transaction(bob, description='Compra do Bob')

        primeira = bob_logged.get(reverse('app:transactions_list'))
        assert primeira.context['object_list'].count() == 1

        segunda = bob_logged.get(reverse('app:transactions_list'), {'page': 2})
        assert segunda.status_code == 404

    def test_overview_nao_soma_transacao_alheia(self, bob_logged, alice, bob, make_transaction):
        make_transaction(alice, value='500.00')
        make_transaction(bob, value='25.00')

        response = bob_logged.get(reverse('app:overview'))

        assert response.context['cards']['outcome'] == 25.00

    def test_overview_nao_soma_investimento_alheio(self, bob_logged, alice, make_investment):
        """O card de investido sai de outra tabela, com filtro próprio: ele erra
        sozinho se o recorte por usuário cair só ali."""
        make_investment(alice)

        response = bob_logged.get(reverse('app:overview'))

        assert response.context['cards']['invested'] == 0.0

    def test_overview_de_usuario_sem_dados_fica_zerado(self, bob_logged, alice, make_transaction, make_investment):
        make_transaction(alice, value='500.00')
        make_investment(alice)

        response = bob_logged.get(reverse('app:overview'))

        cards = response.context['cards']
        assert cards['income'] == 0.0
        assert cards['outcome'] == 0.0
        assert cards['invested'] == 0.0
        assert cards['balance'] == 0.0
        assert response.context['chart_categories'] == []
        assert response.context['chart_months']['labels'] == []

    def test_forecast_nao_preve_gasto_alheio(self, bob_logged, alice, make_installment):
        """A previsão lê crédito: o parcelamento da Alice cai exatamente nela."""
        make_installment(alice)

        response = bob_logged.get(reverse('app:forecast'))

        assert response.context['total'] == 0.0
        assert response.context['chart_categories'] == []


class TestNaoEditaDadoAlheio:
    """Editar transação de outro usuário é 404: fora do queryset, não existe."""

    def test_update_de_transacao_alheia_da_404(self, bob_logged, alice, make_transaction):
        transaction = make_transaction(alice)

        response = bob_logged.post(
            reverse('app:transaction_update', args=[transaction.pk]),
            transaction_payload(transaction),
        )

        assert response.status_code == 404
        transaction.refresh_from_db()
        assert transaction.description == 'Almoco'

    def test_update_barrado_nao_muda_a_titularidade(self, bob_logged, alice, make_transaction):
        """A transação continua da Alice: nada de 404 depois de já ter gravado
        o novo dono."""
        transaction = make_transaction(alice)

        bob_logged.post(
            reverse('app:transaction_update', args=[transaction.pk]),
            transaction_payload(transaction),
        )

        transaction.refresh_from_db()
        assert transaction.user_id == alice.pk

    def test_get_de_transacao_alheia_nao_devolve_formulario(self, bob_logged, alice, make_transaction):
        """O GET destas rotas volta para a lista sem tocar no objeto — nenhum
        campo do registro alheio chega ao HTML."""
        transaction = make_transaction(alice, description='Segredo da Alice')

        response = bob_logged.get(reverse('app:transaction_update', args=[transaction.pk]))

        assert response.status_code == 302
        assert response['Location'] == reverse('app:transactions_list')


class TestNaoApagaDadoAlheio:
    """Apagar transação de outro usuário é 404, e a cascata não dispara."""

    def test_delete_de_transacao_alheia_da_404(self, bob_logged, alice, make_transaction):
        transaction = make_transaction(alice)

        response = bob_logged.post(reverse('app:transaction_delete', args=[transaction.pk]))

        assert response.status_code == 404
        assert Transaction.objects.filter(pk=transaction.pk).exists()

    def test_delete_de_parcela_alheia_nao_apaga_o_parcelamento(self, bob_logged, alice, make_installment):
        """Remover uma parcela apaga o parcelamento inteiro por CASCADE.
        Aplicado ao registro de outro usuário, isso levaria três transações de
        uma vez: o filtro de dono precisa barrar antes de chegar lá.
        """
        installment = make_installment(alice)
        parcel = installment.transactions.first()

        response = bob_logged.post(reverse('app:transaction_delete', args=[parcel.pk]))

        assert response.status_code == 404
        assert Installment.objects.filter(pk=installment.pk).exists()
        assert installment.transactions.count() == 3

    def test_delete_de_perna_alheia_nao_apaga_a_transferencia(self, bob_logged, alice, make_transfer):
        transfer = make_transfer(alice)
        leg = transfer.transactions.first()

        response = bob_logged.post(reverse('app:transaction_delete', args=[leg.pk]))

        assert response.status_code == 404
        assert Transfer.objects.filter(pk=transfer.pk).exists()
        assert transfer.transactions.count() == 2

    def test_delete_de_transacao_de_investimento_alheio_da_404(self, bob_logged, alice, make_investment):
        investment = make_investment(alice)
        transaction = investment.transactions.first()

        response = bob_logged.post(reverse('app:transaction_delete', args=[transaction.pk]))

        assert response.status_code == 404
        assert Investment.objects.filter(pk=investment.pk).exists()


class TestNaoDescobreDadoAlheio:
    """A resposta ao dado alheio é igual à do dado inexistente.

    Um 403 onde o outro caso dá 404 seria um oráculo: bastaria varrer as pks
    para saber quais existem e quantas transações o vizinho tem. Por isso o
    filtro por usuário roda antes de qualquer regra de negócio.
    """

    @pytest.mark.parametrize('name', OBJECT_ROUTES)
    def test_pk_alheia_responde_como_pk_inexistente(self, bob_logged, alice, make_transaction, name):
        transaction = make_transaction(alice)

        alheia = bob_logged.post(reverse(f'app:{name}', args=[transaction.pk]))
        inexistente = bob_logged.post(reverse(f'app:{name}', args=[transaction.pk + 10000]))

        assert alheia.status_code == 404
        assert inexistente.status_code == 404

    def test_derivada_alheia_da_404_e_nao_403(self, bob_logged, derived_transaction):
        """A parcela da Alice é derivada, e derivada tem regra própria (403).
        Para o Bob ela é apenas inexistente: o dono é checado primeiro.
        """
        response = bob_logged.post(
            reverse('app:transaction_update', args=[derived_transaction.pk]),
            transaction_payload(derived_transaction),
        )

        assert response.status_code == 404

    def test_investimento_alheio_da_404_e_nao_403(self, bob_logged, alice, make_investment):
        """Mesmo ponto na exclusão: transação de investimento é 403 para o dono,
        e não pode virar pista para quem não é."""
        transaction = make_investment(alice).transactions.first()

        response = bob_logged.post(reverse('app:transaction_delete', args=[transaction.pk]))

        assert response.status_code == 404


class TestDonoVemDaSessao:
    """A titularidade nasce de request.user; o POST não opina sobre ela."""

    def test_transacao_criada_ignora_user_do_post(self, bob_logged, alice, bob, account, category, business_rules):
        response = bob_logged.post(reverse('app:transaction_create'), {
            'user': alice.pk,
            'account': account.pk,
            'category': category.pk,
            'type': 'OUT',
            'method': 'DEBIT',
            'description': 'Tentativa',
            'value': '10.00',
            'datetime': '2026-01-01T10:00',
        })

        assert response.status_code == 302
        assert Transaction.objects.get(description='Tentativa').user_id == bob.pk

    def test_parcelamento_criado_ignora_user_do_post(self, bob_logged, alice, bob, account, category, business_rules):
        response = bob_logged.post(reverse('app:installment_create'), {
            'user': alice.pk,
            'account': account.pk,
            'category': category.pk,
            'description': 'Parcelado',
            'value': '300.00',
            'installments': 3,
            'datetime': '2026-01-01T10:00',
        })

        assert response.status_code == 302
        installment = Installment.objects.get(description='Parcelado')
        assert installment.user_id == bob.pk

        # As parcelas herdam o dono do parcelamento: se o POST tivesse vencido,
        # o vazamento chegaria à listagem da Alice por três transações.
        assert {t.user_id for t in installment.transactions.all()} == {bob.pk}

    def test_transferencia_criada_ignora_user_do_post(self, bob_logged, alice, bob, account, destination_account, category, business_rules):
        response = bob_logged.post(reverse('app:transfer_create'), {
            'user': alice.pk,
            'origin': account.pk,
            'destination': destination_account.pk,
            'category': category.pk,
            'description': 'Transferido',
            'value': '100.00',
            'datetime': '2026-01-01T10:00',
        })

        assert response.status_code == 302
        transfer = Transfer.objects.get(description='Transferido')
        assert transfer.user_id == bob.pk
        assert {t.user_id for t in transfer.transactions.all()} == {bob.pk}

    def test_criado_pelo_bob_nao_aparece_para_a_alice(self, bob_logged, client, alice, account, category, business_rules):
        """Fecha o ciclo pela leitura: o que o Bob criou não entra na lista da
        Alice, ainda que ele tenha mandado a pk dela no POST."""
        bob_logged.post(reverse('app:transaction_create'), {
            'user': alice.pk,
            'account': account.pk,
            'category': category.pk,
            'type': 'OUT',
            'method': 'DEBIT',
            'description': 'Tentativa',
            'value': '10.00',
            'datetime': '2026-01-01T10:00',
        })

        assert client.login(username='alice', password='senha-de-teste-alice')
        response = client.get(reverse('app:transactions_list'))

        assert response.context['object_list'].count() == 0


class TestSessaoEncerrada:
    """Depois do logout o usuário volta a ser anônimo."""

    def test_logout_fecha_o_acesso(self, alice_logged, alice, make_transaction):
        transaction = make_transaction(alice)
        alice_logged.post(reverse('app:logout'))

        assert_redirected_to_login(alice_logged.get(reverse('app:transactions_list')))
        assert_redirected_to_login(
            alice_logged.post(reverse('app:transaction_delete', args=[transaction.pk]))
        )
        assert Transaction.objects.filter(pk=transaction.pk).exists()


class TestAdminForaDeAlcance:
    """O admin não filtra por usuário: ele lista o movimento de todo mundo.

    O que separa um usuário comum desses dados é a exigência de staff — sem
    ela, o portal seria o caminho mais curto para ver o que é dos outros.
    """

    @pytest.mark.parametrize('name', ['index', 'app_transaction_changelist', 'app_user_changelist'])
    def test_usuario_comum_nao_entra_no_admin(self, alice_logged, alice, make_transaction, name):
        make_transaction(alice)

        response = alice_logged.get(reverse(f'admin:{name}'), follow=True)

        assert response.resolver_match.url_name == 'login'

    def test_usuario_comum_nao_apaga_pelo_admin(self, alice_logged, bob, make_transaction):
        transaction = make_transaction(bob)

        response = alice_logged.post(
            reverse('admin:app_transaction_delete', args=[transaction.pk]),
            {'post': 'yes'},
        )

        assert response.status_code == 302
        assert Transaction.objects.filter(pk=transaction.pk).exists()


class TestLimitesSobreOProprioDado:
    """O outro lado da regra: o que o dono pode fazer com o que é dele.

    Transação derivada não se edita pela tela — os valores dela vêm do registro
    de origem, e mexer numa perna isolada deixaria o conjunto inconsistente. A
    exclusão segue caminho diferente: apaga a origem inteira, que é o gesto
    coerente com o que o usuário vê na linha.
    """

    def test_dono_nao_edita_transacao_derivada(self, alice_logged, derived_transaction):
        response = alice_logged.post(
            reverse('app:transaction_update', args=[derived_transaction.pk]),
            transaction_payload(derived_transaction),
        )

        assert response.status_code == 403
        derived_transaction.refresh_from_db()
        assert derived_transaction.description == 'Notebook'

    def test_dono_apaga_a_parcela_pelo_parcelamento(self, alice_logged, alice, make_installment):
        installment = make_installment(alice)
        parcel = installment.transactions.first()

        response = alice_logged.post(reverse('app:transaction_delete', args=[parcel.pk]))

        assert response.status_code == 302
        assert not Installment.objects.filter(pk=installment.pk).exists()
        assert not Transaction.objects.filter(installment_id=installment.pk).exists()

    def test_dono_apaga_a_perna_pela_transferencia(self, alice_logged, alice, make_transfer):
        transfer = make_transfer(alice)
        leg = transfer.transactions.first()

        response = alice_logged.post(reverse('app:transaction_delete', args=[leg.pk]))

        assert response.status_code == 302
        assert not Transfer.objects.filter(pk=transfer.pk).exists()
        assert not Transaction.objects.filter(transfer_id=transfer.pk).exists()

    def test_dono_nao_apaga_transacao_de_investimento(self, alice_logged, alice, make_investment):
        """Investimento fica fora da cascata: a origem acumula aplicações,
        resgates e rendimentos, e apagar tudo a partir de uma linha seria
        destrutivo demais."""
        investment = make_investment(alice)
        transaction = investment.transactions.first()

        response = alice_logged.post(reverse('app:transaction_delete', args=[transaction.pk]))

        assert response.status_code == 403
        assert Transaction.objects.filter(pk=transaction.pk).exists()
        assert Investment.objects.filter(pk=investment.pk).exists()

    def test_cascata_do_dono_para_na_fronteira(self, alice_logged, alice, bob, make_installment):
        """A exclusão em cascata é a operação mais ampla da tela: vale conferir
        que ela não atravessa para o parcelamento do vizinho."""
        alice_installment = make_installment(alice)
        bob_installment = make_installment(bob)

        alice_logged.post(reverse('app:transaction_delete', args=[
            alice_installment.transactions.first().pk,
        ]))

        assert Installment.objects.filter(pk=bob_installment.pk).exists()
        assert bob_installment.transactions.count() == 3
