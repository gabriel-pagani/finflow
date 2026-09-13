import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from assistant import attachments
from assistant.client import IMAGE_MEMORY, converse, history
from assistant.models import Attachment, Conversation, Message, Role


JPEG = b'\xff\xd8\xff' + b'\x00' * 64
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 64


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def conversation(user):
    return Conversation.objects.create(user=user)


def photo(conversation, text=''):
    message = Message.objects.create(conversation=conversation, role=Role.USER, content=text)
    attachment = attachments.attach(message, attachments.Upload(kind='image', mime='image/jpeg', extension='.jpg', data=JPEG))
    message.items = [attachments.user_item(text, attachment)]
    message.save(update_fields=['items'])
    return attachment


def test_aceita_pelo_conteudo_e_nao_pelo_rotulo():
    upload = attachments.inspect(SimpleUploadedFile('seja-o-que-for.txt', JPEG, content_type='text/plain'))
    assert (upload.kind, upload.mime, upload.extension) == ('image', 'image/jpeg', '.jpg')


def test_recusa_o_que_nao_e_foto_nem_audio():
    with pytest.raises(attachments.UploadError):
        attachments.inspect(SimpleUploadedFile('foto.jpg', b'nao sou uma imagem', content_type='image/jpeg'))


def test_recusa_foto_acima_do_teto():
    with pytest.raises(attachments.UploadError):
        attachments.inspect(SimpleUploadedFile('foto.jpg', JPEG + b'\x00' * attachments.LIMITS['image'], content_type='image/jpeg'))


def test_arquivo_vai_para_a_pasta_do_dono_com_nome_sorteado(conversation, user):
    attachment = photo(conversation)
    assert attachment.file.name.startswith(f'assistant/{user.pk}/')
    assert attachment.file.name.endswith('.jpg')


def test_turno_guarda_a_referencia_e_nao_a_imagem(conversation):
    attachment = photo(conversation, 'segue o cupom')

    items = Message.objects.get(pk=attachment.message_id).items

    assert items[0]['content'][1]['image_url'] == f'attachment:{attachment.pk}'
    assert 'base64' not in json.dumps(items)


def test_so_as_fotos_recentes_voltam_para_o_modelo(conversation):
    for index in range(IMAGE_MEMORY + 2):
        photo(conversation, f'foto {index}')

    parts = [part for item in history(conversation) for part in item['content'] if isinstance(part, dict)]
    imagens = [part for part in parts if part.get('type') == 'input_image']

    assert len(imagens) == IMAGE_MEMORY
    assert all(part['image_url'].startswith('data:image/jpeg;base64,') for part in imagens)
    assert parts.count(attachments.FORGOTTEN) == 2


def test_foto_de_outro_dono_nao_volta_para_o_modelo(conversation, other_user):
    alheia = photo(Conversation.objects.create(user=other_user))
    message = Message.objects.create(conversation=conversation, role=Role.USER, content='e esta?')
    message.items = [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': f'attachment:{alheia.pk}', 'detail': 'high'}]}]
    message.save(update_fields=['items'])

    assert history(conversation)[0]['content'] == [attachments.FORGOTTEN]


def test_foto_cujo_arquivo_sumiu_vira_marcador(conversation):
    attachment = photo(conversation)
    Path(attachment.file.path).unlink()

    assert history(conversation)[0]['content'] == [attachments.FORGOTTEN]


def test_audio_entra_na_conversa_como_transcricao(user, conversation, fake_openai, monkeypatch):
    monkeypatch.setattr('assistant.client.transcribe', lambda upload: 'gastei 30 no mercado')
    fake_openai.turns.append(fake_openai.text_turn('Ok.'))
    upload = attachments.Upload(kind='audio', mime='audio/webm', extension='.webm', data=WEBM)

    events = list(converse(conversation, user, 'anota', upload))

    assert events[0] == {'type': 'transcript', 'text': 'anota\ngastei 30 no mercado'}
    message = conversation.messages.filter(role=Role.USER).get()
    assert message.content == 'anota\ngastei 30 no mercado'
    assert message.items == [{'role': 'user', 'content': 'anota\ngastei 30 no mercado'}]
    assert message.attachment.kind == 'audio'


def test_transcricao_vazia_avisa_sem_chamar_o_modelo(user, conversation, monkeypatch):
    monkeypatch.setattr('assistant.client.client', lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(text='')))))
    upload = attachments.Upload(kind='audio', mime='audio/webm', extension='.webm', data=WEBM)

    events = list(converse(conversation, user, '', upload))

    assert events == [{'type': 'error', 'message': 'Não consegui entender o áudio. Tente gravar de novo.'}]
    assert not conversation.messages.exists()


def test_stream_recusa_arquivo_invalido_antes_de_responder(allowed):
    response = allowed.post(reverse('assistant:stream'), {
        'message': 'olha isso',
        'file': SimpleUploadedFile('planilha.csv', b'a,b,c\n1,2,3', content_type='text/csv'),
    })

    assert response.status_code == 400
    assert response.json()['error']


def test_stream_aceita_foto_sem_legenda(allowed, user, fake_openai):
    fake_openai.turns.append(fake_openai.text_turn('Li o cupom.'))

    response = allowed.post(reverse('assistant:stream'), {'message': '', 'file': SimpleUploadedFile('foto.jpg', JPEG)})
    b''.join(response.streaming_content)

    enviado = fake_openai.calls[0]['input'][0]['content']
    assert enviado[0]['image_url'].startswith('data:image/jpeg;base64,')
    assert Attachment.objects.get().message.conversation.user == user


def test_foto_sem_legenda_aparece_no_historico(allowed, conversation):
    attachment = photo(conversation)

    blocks = allowed.get(reverse('assistant:history')).json()['blocks']

    assert blocks == [{'kind': 'message', 'role': 'user', 'content': '', 'attachment': {'kind': 'image', 'url': reverse('assistant:attachment', args=[attachment.pk])}}]


def test_anexo_e_entregue_ao_dono_pelo_nginx(allowed, conversation):
    attachment = photo(conversation)

    response = allowed.get(reverse('assistant:attachment', args=[attachment.pk]))

    assert response.status_code == 200
    assert response['X-Accel-Redirect'] == f'/protected-media/{attachment.file.name}'
    assert response['Content-Type'] == 'image/jpeg'


def test_anexo_nao_alcanca_outro_usuario(allowed, other_user):
    attachment = photo(Conversation.objects.create(user=other_user))

    assert allowed.get(reverse('assistant:attachment', args=[attachment.pk])).status_code == 404


def test_limpar_a_conversa_apaga_o_arquivo(allowed, conversation):
    caminho = Path(photo(conversation).file.path)

    allowed.post(reverse('assistant:reset'))

    assert not caminho.exists()
