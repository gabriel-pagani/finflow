from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from app.models import BusinessRule, Card, Category, Installment, Method, Transaction, Transfer, Type
from assistant.models import Conversation, Message, Proposal, Role, Status
from assistant.proposals import ProposalError, cancel, confirm, propose


@pytest.fixture
def conversation(user):
    return Conversation.objects.create(user=user)


def rows(summary):
    return {row['label']: row for row in summary['rows']}


def proposta(kind, user, conversation, **arguments):
    response, proposal = propose(kind, user, conversation, arguments)
    assert proposal is not None, response
    return proposal


def recusa(kind, user, conversation, **arguments):
    response, proposal = propose(kind, user, conversation, arguments)
    assert proposal is None
    assert response['ok'] is False
    return response


# Criação ----------------------------------------------------------------------

def test_propor_nao_grava_e_confirmar_grava_o_que_o_card_mostrou(user, account, debit_rule, category, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT',
                        category=category.pk, description='Mercado', value='87.40', occurred_at='2026-09-04')

    assert not Transaction.objects.exists()
    assert rows(proposal.summary)['Valor']['value'] == '87,40'
    assert rows(proposal.summary)['Data da Transação']['value'] == '04/09/2026'

    confirm(proposal)

    transaction = Transaction.objects.get()
    assert (transaction.user, transaction.value, transaction.category, transaction.occurred_at) == (user, Decimal('87.40'), category, date(2026, 9, 4))
    assert proposal.status == Status.CONFIRMED


def test_card_do_credito_mostra_a_data_efetiva(user, account, card, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='CREDIT',
                        card=card.pk, value='50.00', occurred_at='2026-09-04')

    assert rows(proposal.summary)['Data Efetiva']['value'] == card.charge_date(date(2026, 9, 4)).strftime('%d/%m/%Y')


def test_regra_da_conta_vale_como_na_tela(user, account, debit_rule, conversation):
    response = recusa('transaction', user, conversation, action='create', account=account.pk, type='IN', method='DEBIT', value='10.00')

    assert '__all__' in response['errors']
    assert not Proposal.objects.exists()


def test_campo_desconhecido_e_recusado(user, account, debit_rule, conversation):
    response = recusa('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00', parcelas=3)
    assert 'parcelas' in response['error']


def test_parcelamento_mostra_a_divisao_e_as_datas(user, account, card, conversation):
    proposal = proposta('installment', user, conversation, action='create', account=account.pk, card=card.pk,
                        value='1000.00', installments=3, occurred_at='2026-09-04')

    summary = rows(proposal.summary)
    assert summary['Parcelas']['value'] == '2x de 333,33 + 1x de 333,34'
    assert summary['1ª Parcela em']['value'] == card.charge_date(date(2026, 9, 4)).strftime('%d/%m/%Y')
    assert summary['Última Parcela em']['value'] == card.charge_date(date(2026, 11, 4)).strftime('%d/%m/%Y')

    confirm(proposal)
    assert Installment.objects.get().transactions.count() == 3


def test_transferencia_confirmada_gera_as_duas_pernas(user, account, other_account, transfer_out_rule, transfer_in_rule, conversation):
    proposal = proposta('transfer', user, conversation, action='create', origin=account.pk, destination=other_account.pk, value='250.00')

    confirm(proposal)

    assert Transfer.objects.get().transactions.count() == 2


def test_data_omitida_vale_hoje(user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='1.00')
    assert proposal.payload['occurred_at'] == timezone.localdate().isoformat()


# Edição -----------------------------------------------------------------------

def test_edicao_mostra_o_antes_e_mantem_o_resto(user, account, category, make_transaction, conversation):
    transaction = make_transaction(category=category, description='Almoço', value=Decimal('30.00'))
    transaction.save()

    proposal = proposta('transaction', user, conversation, action='update', id=transaction.pk, value='35.50')

    assert rows(proposal.summary)['Valor'] == {'label': 'Valor', 'value': '35,50', 'before': '30,00'}
    assert 'before' not in rows(proposal.summary)['Descrição']

    confirm(proposal)

    transaction.refresh_from_db()
    assert (transaction.value, transaction.description, transaction.category) == (Decimal('35.50'), 'Almoço', category)


def test_null_na_edicao_mantem_o_campo(user, category, make_transaction, conversation):
    transaction = make_transaction(category=category, description='Almoço')
    transaction.save()

    proposal = proposta('transaction', user, conversation, action='update', id=transaction.pk, value='12.00', category=None, description=None, card=None, clear=None)
    confirm(proposal)

    transaction.refresh_from_db()
    assert (transaction.value, transaction.category, transaction.description) == (Decimal('12.00'), category, 'Almoço')


def test_clear_esvazia_a_categoria(user, category, make_transaction, conversation):
    transaction = make_transaction(category=category)
    transaction.save()

    proposal = proposta('transaction', user, conversation, action='update', id=transaction.pk, clear=['category'])
    assert rows(proposal.summary)['Categoria']['before'] == 'Mercado'

    confirm(proposal)
    transaction.refresh_from_db()
    assert transaction.category is None


@pytest.mark.parametrize('clear, extra', [(['value'], {}), (['category'], {'category': 1}), (['account'], {})])
def test_clear_invalido_e_recusado(user, make_transaction, conversation, clear, extra):
    transaction = make_transaction()
    transaction.save()
    assert 'clear' in recusa('transaction', user, conversation, action='update', id=transaction.pk, clear=clear, **extra)['error']


def test_criacao_com_o_enchimento_do_modo_estrito(user, account, card, conversation):
    proposal = proposta('installment', user, conversation, action='create', id=None, occurred_at='2026-09-12', account=account.pk,
                        card=card.pk, category=None, description=None, value='12000.00', installments=10)
    assert rows(proposal.summary)['Parcelas']['value'] == '10x de 1.200,00'


def test_remocao_ignora_os_campos_de_enchimento(user, make_installment, conversation):
    installment = make_installment()
    installment.save()

    # A chamada exatamente como o modelo a mandou e o sistema recusou oito vezes.
    proposal = proposta('installment', user, conversation, action='delete', id=installment.pk, occurred_at='', account=0,
                        card=0, category=None, description=None, value='', installments=0)

    assert proposal.target_id == installment.pk


def test_edicao_sem_mudanca_e_recusada(user, make_transaction, conversation):
    transaction = make_transaction()
    transaction.save()
    assert 'Nada muda' in recusa('transaction', user, conversation, action='update', id=transaction.pk, value='10.00')['error']


def test_cartao_editado_avisa_que_o_ciclo_novo_vale_para_frente(user, card, conversation):
    proposal = proposta('card', user, conversation, action='update', id=card.pk, closing_day=10)

    assert rows(proposal.summary)['Dia de Fechamento'] == {'label': 'Dia de Fechamento', 'value': '10', 'before': '5'}
    assert any('próximas compras' in note for note in proposal.summary['notes'])


def test_parcelamento_nao_se_edita(user, make_installment, conversation):
    installment = make_installment()
    installment.save()
    assert 'Ação inválida' in recusa('installment', user, conversation, action='update', id=installment.pk, value='1.00')['error']


@pytest.mark.parametrize('action, extra', [('update', {'value': '1.00'}), ('delete', {})])
def test_parcela_manda_para_a_origem(user, make_installment, conversation, action, extra):
    installment = make_installment()
    installment.save()

    response = recusa('transaction', user, conversation, action=action, id=installment.transactions.first().pk, **extra)

    assert f'parcelamento {installment.pk}' in response['error']


@pytest.mark.parametrize('action, extra', [('update', {'value': '1.00'}), ('delete', {})])
def test_registro_de_outro_usuario_nao_existe(user, other_user, make_transaction, conversation, action, extra):
    alheia = make_transaction(user=other_user)
    alheia.save()

    assert 'Não existe' in recusa('transaction', user, conversation, action=action, id=alheia.pk, **extra)['error']


# Remoção ----------------------------------------------------------------------

def test_cartao_em_uso_e_recusado_ja_na_proposta(user, card, make_transaction, conversation):
    make_transaction(method=Method.CREDIT, card=card).save()

    assert 'não pode ser removido' in recusa('card', user, conversation, action='delete', id=card.pk)['error']
    assert Card.objects.filter(pk=card.pk).exists()


def test_proposta_de_remocao_nao_apaga_nada(user, make_transaction, conversation):
    transaction = make_transaction()
    transaction.save()

    proposta('transaction', user, conversation, action='delete', id=transaction.pk)

    assert Transaction.objects.filter(pk=transaction.pk).exists()


def test_remocao_do_parcelamento_avisa_e_leva_as_parcelas(user, make_installment, conversation):
    installment = make_installment(installments=4)
    installment.save()

    proposal = proposta('installment', user, conversation, action='delete', id=installment.pk)
    assert any('4 parcelas' in note for note in proposal.summary['notes'])

    confirm(proposal)
    assert not Transaction.objects.exists()


# Confirmação ------------------------------------------------------------------

def test_registro_alterado_depois_da_proposta_nao_e_sobrescrito(user, make_transaction, conversation):
    transaction = make_transaction(value=Decimal('10.00'))
    transaction.save()
    proposal = proposta('transaction', user, conversation, action='update', id=transaction.pk, value='20.00')

    Transaction.objects.filter(pk=transaction.pk).update(description='Mudou em outra aba')

    with pytest.raises(ProposalError):
        confirm(proposal)

    transaction.refresh_from_db()
    assert transaction.value == Decimal('10.00')
    assert proposal.status == Status.FAILED


def test_regra_removida_depois_da_proposta_recusa_a_confirmacao(user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')
    debit_rule.delete()

    with pytest.raises(ProposalError):
        confirm(proposal)

    assert not Transaction.objects.exists()


def test_proposta_nao_se_confirma_duas_vezes(user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')
    confirm(proposal)

    with pytest.raises(ProposalError):
        confirm(proposal)

    assert Transaction.objects.count() == 1


def test_proposta_expirada_nao_se_confirma(user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')
    Proposal.objects.filter(pk=proposal.pk).update(created_at=timezone.now() - Proposal.EXPIRY - timedelta(minutes=1))
    proposal.refresh_from_db()

    with pytest.raises(ProposalError):
        confirm(proposal)

    assert not Transaction.objects.exists()
    assert proposal.state == 'expired'


def test_desfecho_chega_ao_modelo_sem_aparecer_no_chat(user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')
    cancel(proposal)

    note = Message.objects.get(conversation=conversation)
    assert (note.role, note.visible) == (Role.USER, False)
    assert 'Nada foi gravado' in note.content
    assert not Transaction.objects.exists()


# Rotas ------------------------------------------------------------------------

def test_confirmar_pela_rota(allowed, user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')

    response = allowed.post(reverse('assistant:confirm', args=[proposal.pk]))

    assert response.status_code == 200
    assert response.json()['state'] == 'confirmed'
    assert Transaction.objects.count() == 1


def test_proposta_de_outro_usuario_nao_se_confirma(allowed, other_user, account, debit_rule):
    conversa = Conversation.objects.create(user=other_user)
    proposal = proposta('transaction', other_user, conversa, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')

    assert allowed.post(reverse('assistant:confirm', args=[proposal.pk])).status_code == 404
    assert not Transaction.objects.exists()


def test_confirmar_exige_a_permissao(logged, user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')

    assert logged.post(reverse('assistant:confirm', args=[proposal.pk])).status_code == 403
    assert not Transaction.objects.exists()


def test_recusa_na_confirmacao_volta_com_o_motivo(allowed, user, account, debit_rule, conversation):
    proposal = proposta('transaction', user, conversation, action='create', account=account.pk, type='OUT', method='DEBIT', value='10.00')
    debit_rule.delete()

    response = allowed.post(reverse('assistant:confirm', args=[proposal.pk]))

    assert response.status_code == 409
    assert response.json()['state'] == 'failed'
    assert 'não permite' in response.json()['result']


def test_historico_traz_o_card_na_ordem_da_conversa(allowed, user, account, debit_rule, conversation, fake_openai):
    fake_openai.turns.extend([
        fake_openai.tool_turn('propor_transacao', f'{{"action": "create", "account": {account.pk}, "type": "OUT", "method": "DEBIT", "value": "12.00"}}'),
        fake_openai.text_turn('Confira no card.'),
    ])

    stream = allowed.post(reverse('assistant:stream'), {'message': 'Lança 12 no débito'})
    events = [event for event in b''.join(stream.streaming_content).decode().split('\n\n') if event]

    assert any('"type": "proposal"' in event for event in events)
    assert not Transaction.objects.exists()

    blocks = allowed.get(reverse('assistant:history')).json()['blocks']
    assert [block['kind'] for block in blocks] == ['message', 'proposal', 'message']
    assert blocks[1]['state'] == 'open'
