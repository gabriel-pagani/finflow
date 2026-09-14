import json

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from assistant.client import history
from assistant.models import MAX_MESSAGE, Command, Conversation, Message, Role


INSTRUCTIONS = 'O saldo investido é a soma das transações com a categoria Investimentos.'


@pytest.fixture
def command(user):
    return Command.objects.create(user=user, name='saldo-investido', instructions=INSTRUCTIONS)


def eventos(response):
    body = b''.join(response.streaming_content).decode()
    return [json.loads(chunk[len('data: '):]) for chunk in body.split('\n\n') if chunk.startswith('data: ')]


def turno_enviado(fake_openai):
    return fake_openai.calls[0]['input'][-1]['content']


# Gerenciar ----------------------------------------------------------------------

@pytest.mark.parametrize('method, route, args', [
    ('get', 'assistant:commands', []),
    ('post', 'assistant:command_create', []),
    ('post', 'assistant:command_update', [1]),
    ('post', 'assistant:command_delete', [1]),
])
def test_rota_sem_permissao_responde_403(logged, method, route, args):
    assert getattr(logged, method)(reverse(route, args=args)).status_code == 403


def test_cria_edita_e_apaga(allowed, user):
    create = allowed.post(reverse('assistant:command_create'), {'name': 'saldo-investido', 'instructions': INSTRUCTIONS})
    assert create.status_code == 200
    command = Command.objects.get(user=user)
    assert create.json()['command'] == {'id': command.pk, 'name': 'saldo-investido', 'instructions': INSTRUCTIONS}

    update = allowed.post(reverse('assistant:command_update', args=[command.pk]), {'name': 'investido', 'instructions': 'Outra coisa.'})
    assert update.status_code == 200
    command.refresh_from_db()
    assert (command.name, command.instructions) == ('investido', 'Outra coisa.')

    assert allowed.post(reverse('assistant:command_delete', args=[command.pk])).status_code == 200
    assert not Command.objects.exists()


@pytest.mark.parametrize('typed, saved', [
    ('/saldo-investido', 'saldo-investido'),
    ('Saldo Investido', 'saldo-investido'),
    ('saldo_investido', 'saldo-investido'),
    ('previsão--do mês', 'previsao-do-mes'),
])
def test_nome_vira_o_que_se_digita_depois_da_barra(allowed, user, typed, saved):
    allowed.post(reverse('assistant:command_create'), {'name': typed, 'instructions': INSTRUCTIONS})
    assert Command.objects.get(user=user).name == saved


def test_nome_sem_letra_nem_numero_e_recusado(allowed):
    response = allowed.post(reverse('assistant:command_create'), {'name': '/!!', 'instructions': INSTRUCTIONS})

    assert response.status_code == 400
    assert 'letras ou números' in response.json()['error']
    assert not Command.objects.exists()


def test_campo_vazio_diz_qual_e(allowed):
    response = allowed.post(reverse('assistant:command_create'), {'name': 'saldo', 'instructions': ''})

    assert response.status_code == 400
    assert response.json()['error'].startswith('Instruções:')


def test_nome_repetido_e_recusado(allowed, command):
    response = allowed.post(reverse('assistant:command_create'), {'name': 'Saldo Investido', 'instructions': 'Outra.'})

    assert response.status_code == 400
    assert 'já tem um comando com esse nome' in response.json()['error']
    assert Command.objects.count() == 1


def test_mesmo_nome_de_outro_usuario_e_aceito(allowed, other_user):
    Command.objects.create(user=other_user, name='saldo-investido', instructions='Dele.')

    response = allowed.post(reverse('assistant:command_create'), {'name': 'saldo-investido', 'instructions': INSTRUCTIONS})

    assert response.status_code == 200
    assert Command.objects.count() == 2


def test_lista_so_os_proprios_em_ordem_de_nome(allowed, user, other_user):
    Command.objects.create(user=user, name='zeta', instructions='Z.')
    Command.objects.create(user=user, name='alfa', instructions='A.')
    Command.objects.create(user=other_user, name='beta', instructions='Dele.')

    data = allowed.get(reverse('assistant:commands')).json()

    assert [command['name'] for command in data['commands']] == ['alfa', 'zeta']
    assert data['limit'] == Command.LIMIT


def test_instrucoes_nao_passam_do_tamanho_da_mensagem(allowed):
    response = allowed.post(reverse('assistant:command_create'), {'name': 'longo', 'instructions': 'a' * (MAX_MESSAGE + 1)})

    assert response.status_code == 400
    assert response.json()['error'].startswith('Instruções:')
    assert not Command.objects.exists()


def encher(user, total=Command.LIMIT):
    Command.objects.bulk_create(Command(user=user, name=f'comando-{index}', instructions='Algo.') for index in range(total))


def criar(client):
    return client.post(reverse('assistant:command_create'), {'name': 'mais-um', 'instructions': INSTRUCTIONS})


def faixa(user, codename):
    user.user_permissions.add(Permission.objects.get(codename=codename, content_type__app_label='assistant'))


def test_sem_faixa_para_em_cinco(allowed, user):
    encher(user)

    response = criar(allowed)

    assert response.status_code == 400
    assert f'{Command.LIMIT} comandos' in response.json()['error']
    assert Command.objects.count() == Command.LIMIT
    assert allowed.get(reverse('assistant:commands')).json()['limit'] == Command.LIMIT


@pytest.mark.parametrize('codename, limit', [('command_limit_10', 10), ('command_limit_20', 20)])
def test_faixa_da_permissao_define_o_teto(allowed, user, codename, limit):
    faixa(user, codename)
    encher(user, limit - 1)

    assert criar(allowed).status_code == 200
    assert criar(allowed).status_code == 400
    assert Command.objects.count() == limit
    assert allowed.get(reverse('assistant:commands')).json()['limit'] == limit


def test_com_mais_de_uma_faixa_vale_a_maior(allowed, user):
    faixa(user, 'command_limit_10')
    faixa(user, 'command_limit_20')

    assert allowed.get(reverse('assistant:commands')).json()['limit'] == 20


@pytest.mark.parametrize('promote', [
    lambda user: faixa(user, 'unlimited_commands'),
    lambda user: setattr(user, 'is_superuser', True) or user.save(),
], ids=['permissao', 'superusuario'])
def test_ilimitado_nao_tem_teto(allowed, user, promote):
    promote(user)
    encher(user, 20)

    assert criar(allowed).status_code == 200
    assert allowed.get(reverse('assistant:commands')).json()['limit'] is None


def test_quem_perde_a_faixa_mantem_os_comandos_mas_nao_cria(allowed, user, command, fake_openai):
    faixa(user, 'command_limit_10')
    encher(user, 7)
    user.user_permissions.remove(Permission.objects.get(codename='command_limit_10'))

    assert criar(allowed).status_code == 400
    assert Command.objects.count() == 8

    update = allowed.post(reverse('assistant:command_update', args=[command.pk]), {'name': command.name, 'instructions': 'Novo.'})
    assert update.status_code == 200

    fake_openai.turns.append(fake_openai.text_turn('Ok.'))
    b''.join(allowed.post(reverse('assistant:stream'), {'message': '/saldo-investido'}).streaming_content)
    assert 'Novo.' in turno_enviado(fake_openai)


def test_comandos_de_outro_usuario_nao_contam_no_limite(allowed, other_user):
    encher(other_user)

    assert criar(allowed).status_code == 200


def test_comando_de_outro_usuario_nao_se_edita_nem_se_apaga(allowed, other_user):
    alheio = Command.objects.create(user=other_user, name='saldo', instructions='Dele.')

    update = allowed.post(reverse('assistant:command_update', args=[alheio.pk]), {'name': 'meu', 'instructions': 'Meu.'})
    delete = allowed.post(reverse('assistant:command_delete', args=[alheio.pk]))

    assert (update.status_code, delete.status_code) == (404, 404)
    alheio.refresh_from_db()
    assert (alheio.user, alheio.name) == (other_user, 'saldo')


def test_so_a_pagina_traz_o_gerenciador(allowed):
    page = allowed.get(reverse('assistant:page')).content.decode()
    assert 'assistant-commands-open' in page
    assert f'maxlength="{MAX_MESSAGE}"' in page

    overview = allowed.get(reverse('app:overview')).content.decode()
    assert 'assistant-suggestions' in overview
    assert 'assistant-commands-open' not in overview


# Chamar -------------------------------------------------------------------------

def test_comando_leva_as_instrucoes_ao_modelo_e_o_chat_mostra_o_digitado(allowed, user, command, fake_openai):
    fake_openai.turns.append(fake_openai.text_turn('Seu saldo investido é 1.000,00.'))

    events = eventos(allowed.post(reverse('assistant:stream'), {'message': '/saldo-investido'}))

    assert [event['type'] for event in events] == ['delta', 'done']
    enviado = turno_enviado(fake_openai)
    assert '/saldo-investido' in enviado
    assert INSTRUCTIONS in enviado
    assert 'Complemento' not in enviado

    assert Message.objects.get(role=Role.USER).content == '/saldo-investido'
    assert allowed.get(reverse('assistant:history')).json()['blocks'][0]['content'] == '/saldo-investido'


def test_o_que_vem_depois_do_nome_complementa(allowed, command, fake_openai):
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    b''.join(allowed.post(reverse('assistant:stream'), {'message': '/Saldo-Investido  só em 2026'}).streaming_content)

    enviado = turno_enviado(fake_openai)
    assert INSTRUCTIONS in enviado
    assert enviado.endswith('Complemento: só em 2026')


def test_comando_inexistente_e_recusado_antes_do_modelo(allowed, fake_openai):
    response = allowed.post(reverse('assistant:stream'), {'message': '/saldo-investido'})

    assert response.status_code == 400
    assert '/saldo-investido' in response.json()['error']
    assert not Conversation.objects.exists()
    assert fake_openai.calls == []


def test_comando_de_outro_usuario_nao_se_chama(allowed, other_user, fake_openai):
    Command.objects.create(user=other_user, name='saldo-investido', instructions='Segredo dele.')

    response = allowed.post(reverse('assistant:stream'), {'message': '/saldo-investido'})

    assert response.status_code == 400
    assert fake_openai.calls == []


@pytest.mark.parametrize('text', ['paguei 1/2 do aluguel', '/ quanto gastei?', '/saldo-investido?'])
def test_barra_fora_do_comeco_do_nome_e_conversa(allowed, command, fake_openai, text):
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))

    response = allowed.post(reverse('assistant:stream'), {'message': text})
    b''.join(response.streaming_content)

    assert response.status_code == 200
    assert turno_enviado(fake_openai) == text


def test_editar_o_comando_nao_reescreve_a_chamada_anterior(allowed, user, command, fake_openai):
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))
    b''.join(allowed.post(reverse('assistant:stream'), {'message': '/saldo-investido'}).streaming_content)

    command.instructions = 'Instruções novas.'
    command.save()

    enviado = history(Conversation.objects.get(user=user))[0]['content']
    assert INSTRUCTIONS in enviado
    assert 'Instruções novas.' not in enviado
