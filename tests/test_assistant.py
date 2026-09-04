"""O assistente: quem alcança, o que ele enxerga, e o que só o clique grava.

Nenhum teste aqui fala com a OpenAI. O que se verifica é o que fica do lado de
cá: as ferramentas, que são funções com o usuário no argumento, e as rotas, que
decidem permissão e escrita. O laço de conversa é o pedaço que depende de rede, e
não é ele que pode gravar dinheiro errado.
"""

import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
import pytest

from app.assistant import attachments, tools
from app.assistant.client import IMAGE_MEMORY, history
from app.assistant.data import FilterError, options
from app.management.commands.prune_attachments import ORPHAN_GRACE
from app.models import Attachment, Conversation, Installment, Message, Method, PendingWrite, Role, Transaction, Transfer, Type


@pytest.fixture
def use_assistant():
    return Permission.objects.get(codename='use_assistant')


@pytest.fixture
def alice_allowed(alice, use_assistant, client):
    alice.user_permissions.add(use_assistant)
    assert client.login(username='alice', password='senha-de-teste-alice')
    return client


@pytest.fixture
def conversation(alice):
    return Conversation.objects.create(user=alice)


def call(name, arguments, user, conversation):
    return tools.run(name, arguments, user=user, today=timezone.localdate(), conversation=conversation)


# --------------------------------------------------------------------------
# Permissão
# --------------------------------------------------------------------------

ROUTES = ['app:assistant_stream', 'app:assistant_history', 'app:assistant_reset']


@pytest.mark.parametrize('route', ROUTES)
def test_sem_permissao_recusa(alice_logged, route):
    """Logado, mas sem a permissão, é 403 — e não um redirect para o login.

    O redirect padrão do Django devolveria o HTML do login dentro de um fetch,
    que o chat mostraria como se fosse resposta.
    """
    response = alice_logged.post(reverse(route)) if route != 'app:assistant_history' else alice_logged.get(reverse(route))
    assert response.status_code == 403


@pytest.mark.parametrize('route', ROUTES)
def test_sem_sessao_recusa(client, route):
    response = client.get(reverse(route))
    assert response.status_code in (302, 403)


def test_com_permissao_alcanca(alice_allowed):
    response = alice_allowed.get(reverse('app:assistant_history'))
    assert response.status_code == 200
    assert response.json() == {'blocks': []}


def test_botao_so_aparece_com_permissao(alice_logged, alice, use_assistant):
    """O botão flutuante segue a mesma permissão das rotas.

    Escondê-lo não é a proteção — as views recusam de qualquer jeito —, mas
    oferecer um botão que responde 403 seria pior do que não oferecer.
    """
    assert 'id="assistant"' not in alice_logged.get(reverse('app:overview')).content.decode()

    alice.user_permissions.add(use_assistant)
    assert 'id="assistant"' in alice_logged.get(reverse('app:overview')).content.decode()


def test_pagina_sem_permissao_recusa(alice_logged):
    """A aba do assistente segue a mesma permissão do resto do chat.

    403, e não redirect: quem já entrou no sistema e não pode usar o assistente
    não tem o que fazer na tela de login.
    """
    assert alice_logged.get(reverse('app:assistant')).status_code == 403


def test_pagina_sem_sessao_manda_para_o_login(client):
    """Aqui o redirect é o certo: é uma página, e quem a abriu não fez login."""
    response = client.get(reverse('app:assistant'))
    assert response.status_code == 302
    assert reverse('app:login') in response['Location']


def test_link_do_menu_so_aparece_com_permissao(alice_logged, alice, use_assistant):
    assert reverse('app:assistant') not in alice_logged.get(reverse('app:overview')).content.decode()

    alice.user_permissions.add(use_assistant)
    assert reverse('app:assistant') in alice_logged.get(reverse('app:overview')).content.decode()


def test_pagina_traz_o_chat_sem_o_atalho(alice_allowed):
    """Na página o painel vem embutido, e o botão flutuante não vem junto.

    Os dois na mesma tela seriam dois chats sobrepostos: o atalho abriria um
    segundo painel por cima do que já está aberto ali.
    """
    page = alice_allowed.get(reverse('app:assistant'))

    assert page.status_code == 200
    assert 'assistant-embedded' in page.content.decode()
    assert 'assistant-toggle' not in page.content.decode()

    outra = alice_allowed.get(reverse('app:overview')).content.decode()
    assert 'assistant-floating' in outra
    assert 'assistant-toggle' in outra



# --------------------------------------------------------------------------
# Isolamento
# --------------------------------------------------------------------------

def test_consulta_ve_apenas_o_proprio_dinheiro(alice, bob, conversation, make_transaction):
    """O recorte por usuário é o argumento da consulta, não uma regra do prompt."""
    make_transaction(alice, value=Decimal('25.00'))
    make_transaction(bob, value=Decimal('999.00'))

    payload, pending = call('consultar_financas', {'months': '1'}, alice, conversation)

    assert pending is None
    assert payload['summary']['outcome'] == Decimal('25.00')
    assert payload['summary']['transactions'] == 1


def test_opcoes_veem_apenas_os_proprios_cartoes(alice, bob, make_card):
    make_card(alice, last_digits='1111')
    make_card(bob, last_digits='2222')

    digits = [card['last_digits'] for card in options(alice, timezone.localdate())['cards']]

    assert digits == ['1111']


def test_filtro_invalido_volta_para_o_modelo(alice, conversation):
    """Parâmetro errado é resposta, não exceção.

    A mensagem diz o que o campo aceita, e é o próprio modelo quem corrige e
    chama de novo — derrubar a conversa por isso perderia o histórico inteiro.
    """
    payload, pending = call('consultar_financas', {'type': 'ENTRADA'}, alice, conversation)

    assert pending is None
    assert payload['ok'] is False
    assert payload['error'] == 'filtro_invalido'
    assert 'IN' in payload['message']


def test_ferramenta_desconhecida_nao_estoura(alice, conversation):
    payload, pending = call('apagar_tudo', {}, alice, conversation)

    assert pending is None
    assert payload['ok'] is False


# --------------------------------------------------------------------------
# Propor não é gravar
# --------------------------------------------------------------------------

def test_registrar_nao_grava_nada(alice, conversation, account, category, business_rules):
    """O centro da feature: a ferramenta monta, e o banco continua vazio."""
    payload, pending = call('registrar_lancamento', {
        'kind': 'transaction',
        'account': account.pk,
        'category': category.pk,
        'type': Type.OUT,
        'method': Method.DEBIT,
        'value': '25.00',
        'description': 'Almoço',
    }, alice, conversation)

    assert payload['ok'] is True
    assert payload['status'] == 'aguardando_confirmacao'
    assert pending.status == PendingWrite.Status.PENDING
    assert not Transaction.objects.exists()


def test_registro_invalido_nao_gera_pendente(alice, conversation, account, category, business_rules):
    """Valor negativo é recusado na proposta: o cartão nunca chega à tela."""
    payload, pending = call('registrar_lancamento', {
        'kind': 'transaction',
        'account': account.pk,
        'category': category.pk,
        'type': Type.OUT,
        'method': Method.DEBIT,
        'value': '-25.00',
    }, alice, conversation)

    assert payload['ok'] is False
    assert 'value' in payload['errors']
    assert pending is None
    assert not PendingWrite.objects.exists()


def test_credito_mostra_o_vencimento_no_cartao(alice, conversation, account, category, business_rules, make_card):
    """O resumo precisa dizer a data que será gravada, não a que foi falada.

    No crédito o formulário troca a data da compra pelo vencimento da fatura, e
    confirmar sem ver isso registraria o lançamento semanas fora do lugar.
    """
    card = make_card(alice)

    payload, pending = call('registrar_lancamento', {
        'kind': 'transaction',
        'account': account.pk,
        'category': category.pk,
        'card': card.pk,
        'type': Type.OUT,
        'method': Method.CREDIT,
        'value': '100.00',
        'datetime': '2026-03-05T10:00',
    }, alice, conversation)

    assert payload['ok'] is True
    assert 'vencimento' in pending.summary['note']

    data = dict((row['label'], row['value']) for row in pending.summary['rows'])
    assert data['Data'].startswith('27/03/2026')


def test_cartao_de_outro_usuario_e_recusado(alice, bob, conversation, account, business_rules, make_card):
    """A validação é a mesma da tela, e a tela já não aceita cartão alheio."""
    payload, pending = call('registrar_lancamento', {
        'kind': 'transaction',
        'account': account.pk,
        'card': make_card(bob).pk,
        'type': Type.OUT,
        'method': Method.CREDIT,
        'value': '100.00',
    }, alice, conversation)

    assert payload['ok'] is False
    assert pending is None


# --------------------------------------------------------------------------
# Confirmação
# --------------------------------------------------------------------------

@pytest.fixture
def pending_transaction(alice, conversation, account, category, business_rules):
    _, pending = call('registrar_lancamento', {
        'kind': 'transaction',
        'account': account.pk,
        'category': category.pk,
        'type': Type.OUT,
        'method': Method.DEBIT,
        'value': '25.00',
        'description': 'Almoço',
    }, alice, conversation)
    return pending


def test_confirmacao_grava(alice_allowed, pending_transaction):
    response = alice_allowed.post(reverse('app:assistant_confirm', args=[pending_transaction.pk]))

    assert response.status_code == 200
    assert Transaction.objects.count() == 1

    transaction = Transaction.objects.get()
    assert transaction.user == pending_transaction.user
    assert transaction.value == Decimal('25.00')

    pending_transaction.refresh_from_db()
    assert pending_transaction.status == PendingWrite.Status.CONFIRMED


def test_confirmar_duas_vezes_grava_uma(alice_allowed, pending_transaction):
    """O pendente é consumido na primeira confirmação.

    Sem isso, um duplo clique ou um retry do navegador lançaria a mesma compra
    duas vezes — e num sistema de finanças isso não é um registro repetido, é
    dinheiro que some do saldo.
    """
    url = reverse('app:assistant_confirm', args=[pending_transaction.pk])

    assert alice_allowed.post(url).status_code == 200
    assert alice_allowed.post(url).status_code == 409
    assert Transaction.objects.count() == 1


def test_pendente_de_outro_usuario_nao_e_confirmavel(bob, use_assistant, client, pending_transaction):
    """Confirmar o pendente alheio não é uma checagem: é um filtro que não acha."""
    bob.user_permissions.add(use_assistant)
    assert client.login(username='bob', password='senha-de-teste-bob')

    response = client.post(reverse('app:assistant_confirm', args=[pending_transaction.pk]))

    assert response.status_code == 404
    assert not Transaction.objects.exists()


def test_pendente_expirado_nao_grava(alice_allowed, pending_transaction):
    """Entre montar e clicar, o saldo mudou e o usuário já não lembra do quê."""
    PendingWrite.objects.filter(pk=pending_transaction.pk).update(
        created=timezone.now() - PendingWrite.EXPIRY - timedelta(minutes=1),
    )

    response = alice_allowed.post(reverse('app:assistant_confirm', args=[pending_transaction.pk]))

    assert response.status_code == 409
    assert not Transaction.objects.exists()


def test_descartar_nao_grava(alice_allowed, pending_transaction):
    response = alice_allowed.post(reverse('app:assistant_cancel', args=[pending_transaction.pk]))

    assert response.status_code == 200
    assert not Transaction.objects.exists()

    pending_transaction.refresh_from_db()
    assert pending_transaction.status == PendingWrite.Status.CANCELLED


def test_descartado_nao_pode_ser_confirmado(alice_allowed, pending_transaction):
    alice_allowed.post(reverse('app:assistant_cancel', args=[pending_transaction.pk]))
    response = alice_allowed.post(reverse('app:assistant_confirm', args=[pending_transaction.pk]))

    assert response.status_code == 409
    assert not Transaction.objects.exists()


def test_parcelamento_confirmado_gera_as_parcelas(alice_allowed, alice, conversation, account, business_rules, make_card):
    """A confirmação usa o mesmo save da tela, que é quem gera as filhas."""
    card = make_card(alice)

    _, pending = call('registrar_lancamento', {
        'kind': 'installment',
        'account': account.pk,
        'card': card.pk,
        'value': '300.00',
        'installments': 3,
        'description': 'Notebook',
    }, alice, conversation)

    assert not Transaction.objects.exists()

    alice_allowed.post(reverse('app:assistant_confirm', args=[pending.pk]))

    assert Installment.objects.count() == 1
    assert Transaction.objects.count() == 3
    assert sum(t.value for t in Transaction.objects.all()) == Decimal('300.00')


def test_transferencia_confirmada_gera_as_duas_pernas(alice_allowed, alice, conversation, account, destination_account, business_rules):
    _, pending = call('registrar_lancamento', {
        'kind': 'transfer',
        'origin': account.pk,
        'destination': destination_account.pk,
        'value': '300.00',
        'description': 'Reserva',
    }, alice, conversation)

    alice_allowed.post(reverse('app:assistant_confirm', args=[pending.pk]))

    assert Transfer.objects.count() == 1
    assert Transaction.objects.count() == 2


# --------------------------------------------------------------------------
# Histórico
# --------------------------------------------------------------------------

def test_historico_nao_expoe_conversa_de_ferramenta(alice_allowed, alice, conversation):
    """JSON de consulta na tela é o vazamento técnico que o prompt proíbe."""
    Message.objects.create(
        conversation=conversation, role=Role.USER, content='quanto gastei?',
        items=[{'role': 'user', 'content': 'quanto gastei?'}],
    )
    Message.objects.create(
        conversation=conversation, role=Role.TOOL, content='{"summary": {}}',
        items=[{'type': 'function_call_output', 'call_id': 'x', 'output': '{"summary": {}}'}],
    )
    Message.objects.create(
        conversation=conversation, role=Role.ASSISTANT, content='Você gastou R$ 25,00.',
        items=[{'type': 'message', 'role': 'assistant', 'content': []}],
    )

    roles = [block['role'] for block in alice_allowed.get(reverse('app:assistant_history')).json()['blocks']]

    assert roles == ['user', 'assistant']


def test_confirmacao_fala_com_o_modelo_e_nao_com_a_tela(alice_allowed, pending_transaction):
    """O resultado do clique é para o modelo ler, não para o usuário reler.

    Sem a mensagem, o modelo segue achando o lançamento pendente e oferece
    registrar de novo o que já registrou. Com ela na tela, o usuário vê a
    própria ação narrada de volta em terceira pessoa, no lugar onde o cartão
    deveria ter ficado.
    """
    alice_allowed.post(reverse('app:assistant_confirm', args=[pending_transaction.pk]))

    nota = Message.objects.filter(visible=False).get()
    assert 'FOI registrado' in nota.content
    # O que vai ao modelo são os itens; sem eles a mensagem não existe para ele.
    assert nota.items and nota.items[0]['role'] == 'user'

    blocos = alice_allowed.get(reverse('app:assistant_history')).json()['blocks']
    assert not any('FOI registrado' in (b.get('content') or '') for b in blocos)


def test_cartao_confirmado_continua_na_conversa(alice_allowed, pending_transaction):
    """Recarregar não pode engolir o cartão do lançamento já confirmado."""
    alice_allowed.post(reverse('app:assistant_confirm', args=[pending_transaction.pk]))

    blocos = alice_allowed.get(reverse('app:assistant_history')).json()['blocks']
    cartoes = [b for b in blocos if b['kind'] == 'pending']

    assert len(cartoes) == 1
    assert cartoes[0]['state'] == 'confirmed'
    assert cartoes[0]['label']


def test_blocos_vem_em_ordem_de_tempo(alice_allowed, alice, conversation, pending_transaction):
    """Cartão e mensagem convivem numa lista só, senão o cartão vai para o fim."""
    Message.objects.create(
        conversation=conversation, role=Role.ASSISTANT, content='Confirme o lançamento.',
        items=[{'type': 'message', 'role': 'assistant', 'content': []}],
    )

    blocos = alice_allowed.get(reverse('app:assistant_history')).json()['blocks']

    assert [b['kind'] for b in blocos] == ['pending', 'message']


def test_limpar_apaga_so_a_propria_conversa(alice_allowed, bob, conversation):
    Conversation.objects.create(user=bob)

    alice_allowed.post(reverse('app:assistant_reset'))

    assert not Conversation.objects.filter(user=conversation.user).exists()
    assert Conversation.objects.filter(user=bob).exists()


# --------------------------------------------------------------------------
# Anexos
#
# Foto e áudio chegam pela mesma rota da conversa, mas o que se verifica aqui é
# só o lado de cá: o que é aceito, onde o arquivo para, quem alcança e o que
# volta para o modelo. A transcrição e a leitura da imagem são a parte que
# depende de rede, e seguem fora da suíte.
# --------------------------------------------------------------------------

JPEG = b'\xff\xd8\xff' + b'\x00' * 64


@pytest.fixture
def media_root(settings, tmp_path):
    """Cada teste grava na própria pasta, e não no volume da aplicação."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def bob_allowed(bob, use_assistant, client):
    bob.user_permissions.add(use_assistant)
    assert client.login(username='bob', password='senha-de-teste-bob')
    return client


def photo(conversation, text='', data=JPEG):
    """Uma mensagem do usuário com foto, montada como o `converse` a monta."""
    message = Message.objects.create(conversation=conversation, role=Role.USER, content=text, items=[])
    upload = attachments.Upload(kind='image', mime='image/jpeg', extension='.jpg', data=data)
    attachment = attachments.attach(message, upload)
    message.items = [attachments.user_item(text, attachment)]
    message.save(update_fields=['items'])
    return attachment


def test_aceita_foto_pelo_conteudo(media_root):
    upload = attachments.inspect(SimpleUploadedFile('seja-o-que-for.txt', JPEG, content_type='text/plain'))

    assert (upload.kind, upload.mime, upload.extension) == ('image', 'image/jpeg', '.jpg')


def test_recusa_o_que_nao_e_foto_nem_audio():
    """O `content_type` do formulário é palavra de quem envia, e não vale prova."""
    junk = SimpleUploadedFile('foto.jpg', b'nao sou uma imagem', content_type='image/jpeg')

    with pytest.raises(attachments.UploadError):
        attachments.inspect(junk)


def test_recusa_foto_acima_do_teto():
    grande = SimpleUploadedFile('foto.jpg', JPEG + b'\x00' * (8 * 1024 * 1024), content_type='image/jpeg')

    with pytest.raises(attachments.UploadError):
        attachments.inspect(grande)


def test_turno_guarda_referencia_e_nao_a_imagem(media_root, conversation):
    """A foto vai para o disco; o que fica no banco é o número da linha dela."""
    attachment = photo(conversation, 'segue o cupom')

    items = Message.objects.get(pk=attachment.message_id).items
    parts = items[0]['content']

    assert parts[1]['image_url'] == f'attachment:{attachment.pk}'
    assert 'base64' not in json.dumps(items)


def test_so_as_fotos_recentes_voltam_para_o_modelo(media_root, conversation):
    """A antiga vira marcador: o arquivo continua, a imagem sai do contexto."""
    fotos = [photo(conversation, f'foto {index}') for index in range(IMAGE_MEMORY + 2)]

    imagens = [
        part
        for item in history(conversation)
        if isinstance(item.get('content'), list)
        for part in item['content']
        if part.get('type') == 'input_image'
    ]

    assert len(imagens) == IMAGE_MEMORY
    assert all(part['image_url'].startswith('data:image/jpeg;base64,') for part in imagens)
    assert len(fotos) > IMAGE_MEMORY


def test_foto_sem_legenda_continua_no_historico(media_root, alice_allowed, conversation):
    """Mensagem vazia é descartada do chat; vazia com anexo, não — ela É a mensagem."""
    photo(conversation)

    blocos = alice_allowed.get(reverse('app:assistant_history')).json()['blocks']

    assert [bloco['attachment']['kind'] for bloco in blocos] == ['image']


def test_anexo_alcanca_o_dono(media_root, alice_allowed, conversation):
    attachment = photo(conversation)

    response = alice_allowed.get(reverse('app:assistant_attachment', args=[attachment.pk]))

    assert response.status_code == 200
    # Quem entrega o arquivo é o nginx; o Django só diz que pode e onde está.
    assert response['X-Accel-Redirect'].startswith('/protected-media/')
    assert response['Content-Type'] == 'image/jpeg'


def test_anexo_nao_alcanca_quem_nao_e_dono(media_root, alice, bob_allowed):
    """Comprovante é documento financeiro: a URL não pode ser a chave."""
    attachment = photo(Conversation.objects.create(user=alice))

    response = bob_allowed.get(reverse('app:assistant_attachment', args=[attachment.pk]))

    assert response.status_code == 404


def test_limpar_conversa_apaga_o_arquivo(media_root, alice_allowed, conversation):
    """Sem isto o usuário manda apagar e o disco segue guardando a foto dos gastos dele."""
    caminho = Path(photo(conversation).file.path)
    assert caminho.exists()

    alice_allowed.post(reverse('app:assistant_reset'))

    assert not caminho.exists()


# --------------------------------------------------------------------------
# Expiração dos anexos
#
# O comando é o que impede o volume de guardar para sempre o comprovante de
# alguém. O que se verifica aqui é o corte: o que vence sai, o que é recente
# fica, e o histórico continua legível sem o arquivo.
# --------------------------------------------------------------------------


def envelhece(attachment, dias):
    """`created` é auto_now_add: não há como nascer velho, só como ser envelhecido."""
    quando = timezone.now() - timedelta(days=dias)
    Attachment.objects.filter(pk=attachment.pk).update(created=quando)


def test_anexo_vencido_sai_e_o_recente_fica(media_root, conversation):
    velho = photo(conversation)
    novo = photo(conversation)
    envelhece(velho, 120)

    call_command('prune_attachments', days=90)

    assert not Path(velho.file.path).exists()
    assert Path(novo.file.path).exists()
    assert Attachment.objects.count() == 1


def test_retencao_zero_guarda_para_sempre(media_root, conversation):
    """O escape de quem não quer expiração nenhuma: o comportamento de antes."""
    attachment = photo(conversation)
    envelhece(attachment, 3650)

    call_command('prune_attachments', days=0)

    assert Path(attachment.file.path).exists()


def test_a_mensagem_sobrevive_ao_anexo_vencido(media_root, conversation):
    """O arquivo é que é pesado. A conversa que ele gerou não se apaga junto."""
    attachment = photo(conversation, 'mercado, 82 reais')
    envelhece(attachment, 120)

    call_command('prune_attachments', days=90)

    message = Message.objects.get(pk=attachment.message_id)
    assert message.content == 'mercado, 82 reais'
    # No contexto do modelo a foto vira o marcador, e não uma exceção subindo.
    assert attachments.FORGOTTEN in history(conversation)[0]['content']


def test_arquivo_sem_dono_e_recolhido(media_root, conversation):
    """Gravação que passou pelo disco e não chegou ao banco não fica lá para sempre."""
    orfao = media_root / 'assistant' / '99' / 'perdido.jpg'
    orfao.parent.mkdir(parents=True)
    orfao.write_bytes(JPEG)
    antigo = (timezone.now() - ORPHAN_GRACE - timedelta(hours=1)).timestamp()
    os.utime(orfao, (antigo, antigo))

    call_command('prune_attachments')

    assert not orfao.exists()


def test_arquivo_sem_dono_recente_sobrevive(media_root, conversation):
    """Entre o arquivo e a linha há um instante sem dono: é o envio, não um órfão."""
    attachment = photo(conversation)
    orfao = media_root / 'assistant' / '99' / 'agora.jpg'
    orfao.parent.mkdir(parents=True)
    orfao.write_bytes(JPEG)

    call_command('prune_attachments')

    assert orfao.exists()
    assert Path(attachment.file.path).exists()


def test_dry_run_nao_apaga(media_root, conversation):
    attachment = photo(conversation)
    envelhece(attachment, 120)

    call_command('prune_attachments', days=90, dry_run=True)

    assert Path(attachment.file.path).exists()


def test_microfone_e_camera_liberados_para_a_propria_origem(alice_allowed):
    """A gravação e a foto do chat dependem disto.

    Negado no cabeçalho, o navegador recusa `getUserMedia` antes mesmo de
    perguntar ao usuário — e a permissão concedida nas configurações do site não
    muda nada, porque a política do documento vem antes dela.

    A câmera entra pelo mesmo motivo, ainda que o site não chame `getUserMedia`
    com vídeo: o Chrome do Android também prende a esta política a opção de
    câmera do seletor de arquivo, e negada ela some — sobra a galeria.
    """
    policy = alice_allowed.get(reverse('app:overview'))['Permissions-Policy']

    assert 'microphone=(self)' in policy
    assert 'camera=(self)' in policy


def test_stream_recusa_arquivo_invalido(alice_allowed):
    """A recusa vem antes do primeiro byte da resposta, com frase para a tela."""
    response = alice_allowed.post(reverse('app:assistant_stream'), {
        'message': 'olha isso',
        'file': SimpleUploadedFile('planilha.csv', b'a,b,c\n1,2,3', content_type='text/csv'),
    })

    assert response.status_code == 400
    assert response.json()['error']


def test_stream_recusa_mensagem_sem_texto_e_sem_anexo(alice_allowed):
    response = alice_allowed.post(reverse('app:assistant_stream'), {'message': '   '})

    assert response.status_code == 400
