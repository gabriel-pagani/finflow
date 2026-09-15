"""
O assistente no admin, aberto só para depuração.

Os models ficam na seção Assistente, e nada ali se cria nem se edita: mudar uma
mensagem reescreveria o histórico que volta ao modelo.
"""
import json
from datetime import date

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory

from assistant.admin import MessageAdmin
from assistant.models import Action, Attachment, Command, Conversation, DailyUsage, Kind, Message, Proposal, Role, Status


MODELS = [Conversation, Message, Proposal, Attachment, Command, DailyUsage]


@pytest.fixture
def request_de_admin(db):
    from django.contrib.auth import get_user_model

    request = RequestFactory().get('/')
    request.user = get_user_model().objects.create_superuser(username='admin', password='segredo')
    return request


@pytest.mark.parametrize('model', MODELS)
def test_models_do_assistente_ficam_na_secao_assistente(model):
    assert site.is_registered(model)
    assert model._meta.app_config.verbose_name == 'Assistente'


@pytest.mark.parametrize('model', MODELS)
def test_admin_do_assistente_so_le_e_apaga(model, request_de_admin):
    model_admin = site._registry[model]

    assert not model_admin.has_add_permission(request_de_admin)
    assert not model_admin.has_change_permission(request_de_admin)
    assert model_admin.has_view_permission(request_de_admin)
    assert model_admin.has_delete_permission(request_de_admin)


def test_uso_diario_mostra_o_limite_de_agora(user, request_de_admin):
    model_admin = site._registry[DailyUsage]

    assert model_admin.limit(DailyUsage(user=user, day=date(2026, 9, 15))) == DailyUsage.LIMIT
    assert model_admin.limit(DailyUsage(user=request_de_admin.user, day=date(2026, 9, 15))) == 'Ilimitado'


@pytest.fixture
def conversa(user):
    return Conversation.objects.create(user=user)


def linha(conversation, role, content='', items=None, visible=True):
    return Message.objects.create(conversation=conversation, role=role, content=content, items=items or [], visible=visible)


def mostrar(message):
    model_admin = MessageAdmin(Message, site)
    return str(model_admin.step(message)), str(model_admin.detail(message))


def test_linha_do_tempo_filtra_so_por_usuario():
    assert MessageAdmin.list_filter == ('conversation__user',)


def test_chamada_mostra_a_ferramenta_e_so_os_argumentos_usados(conversa):
    chamada = linha(conversa, Role.ASSISTANT, items=[
        {'type': 'reasoning', 'encrypted_content': '...'},
        {'type': 'function_call', 'name': 'listar_transacoes', 'arguments': '{"search": "almoço", "limit": null}', 'call_id': 'a'},
    ])

    etapa, detalhe = mostrar(chamada)

    assert 'Chamada de Ferramenta' in etapa
    assert '<code>listar_transacoes</code>' in detalhe
    assert 'almoço' in detalhe
    assert 'limit' not in detalhe


def rodada(conversation, *chamadas):
    """Grava a rodada do modelo e, em seguida, um retorno por chamada, como o client faz."""
    mensagem = linha(conversation, Role.ASSISTANT, items=[
        {'type': 'function_call', 'name': name, 'arguments': '{}', 'call_id': f'c{index}'}
        for index, (name, _) in enumerate(chamadas)
    ])
    for index, (_, payload) in enumerate(chamadas):
        linha(conversation, Role.TOOL, json.dumps(payload), [{'type': 'function_call_output', 'call_id': f'c{index}', 'output': json.dumps(payload)}])
    return mensagem


def test_cada_chamada_traz_o_proprio_retorno_logo_abaixo(conversa):
    mensagem = rodada(
        conversa,
        ('analisar_transacoes', {'filters': {}, 'total': {'income': '10.00', 'outcome': '4.00', 'net': '6.00', 'count': 2}}),
        ('consultar_cadastros', {'accounts': [{}], 'categories': [{}, {}], 'cards': []}),
    )

    etapa, detalhe = mostrar(mensagem)
    resumo, detalhe = detalhe.split('assistant-timeline-full')

    assert 'Chamada de Ferramenta' in etapa
    assert 'analisar_transacoes → entradas 10.00' in resumo
    analise, cadastros = detalhe.index('<code>analisar_transacoes</code>'), detalhe.index('<code>consultar_cadastros</code>')
    assert analise < detalhe.index('entradas 10.00, saídas 4.00, saldo 6.00, 2 transações') < cadastros
    assert cadastros < detalhe.index('1 contas, 2 categorias, 0 cartões')


def test_retornos_nao_ganham_linha_propria(conversa, request_de_admin):
    rodada(conversa, ('consultar_cadastros', {'accounts': [], 'categories': [], 'cards': []}))

    roles = set(MessageAdmin(Message, site).get_queryset(request_de_admin).values_list('role', flat=True))

    assert roles == {Role.ASSISTANT}


def test_erro_da_ferramenta_se_destaca(conversa):
    mensagem = rodada(conversa, ('propor_cartao', {'ok': False, 'error': 'A conta não permite saída em Crédito.'}))

    etapa, detalhe = mostrar(mensagem)

    assert 'Ferramenta Recusou' in etapa
    assert 'A conta não permite saída em Crédito.' in detalhe


def test_retorno_de_proposta_mostra_a_situacao_atual(user, conversa):
    proposta = Proposal.objects.create(user=user, conversation=conversa, kind=Kind.CARD, action=Action.CREATE,
                                       summary={'title': 'Criar cartão', 'rows': []}, status=Status.CONFIRMED)
    mensagem = rodada(conversa, ('propor_cartao', {'ok': True, 'proposal_id': proposta.pk, 'summary': proposta.summary}))

    _, detalhe = mostrar(mensagem)

    assert f'Proposta #{proposta.pk}' in detalhe
    assert 'Criar cartão' in detalhe
    assert 'Confirmada' in detalhe


def test_comando_mostra_o_digitado_e_o_que_foi_ao_modelo(conversa):
    mensagem = linha(conversa, Role.USER, '/resumo', [{'role': 'user', 'content': '<instrucoes>Resuma o mês.</instrucoes>'}])

    etapa, detalhe = mostrar(mensagem)

    assert 'Usuário' in etapa
    assert '/resumo' in detalhe
    assert 'Texto enviado ao modelo' in detalhe
    assert '&lt;instrucoes&gt;Resuma o mês.&lt;/instrucoes&gt;' in detalhe


def test_aviso_escondido_do_chat_aparece_como_interno(conversa):
    aviso = linha(conversa, Role.USER, 'O usuário confirmou a proposta 1.', [{'role': 'user', 'content': '[O usuário confirmou a proposta 1.]'}], visible=False)

    etapa, _ = mostrar(aviso)

    assert 'Aviso Interno' in etapa


def test_toda_linha_fecha_num_resumo_de_uma_linha_sem_repetir_o_texto(conversa):
    resposta = linha(conversa, Role.ASSISTANT, 'Você tem:\n\n- Carteira\n- Itaú')

    _, detalhe = mostrar(resposta)

    assert '<span class="assistant-timeline-preview">Você tem: - Carteira - Itaú</span>' in detalhe
    assert detalhe.count('Você tem:\n\n- Carteira\n- Itaú') == 1
