import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from assistant import attachments
from assistant.client import history
from assistant.management.commands.prune_attachments import ORPHAN_GRACE
from assistant.models import Attachment, Conversation, Message, Role


JPEG = b'\xff\xd8\xff' + b'\x00' * 64


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


def envelhece(attachment, dias):
    Attachment.objects.filter(pk=attachment.pk).update(created_at=timezone.now() - timedelta(days=dias))


def orfao(media_root, nome, horas=None):
    caminho = media_root / 'assistant' / '99' / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(JPEG)
    if horas is not None:
        antigo = (timezone.now() - timedelta(hours=horas)).timestamp()
        os.utime(caminho, (antigo, antigo))
    return caminho


def test_vencido_sai_e_recente_fica(conversation):
    velho, novo = photo(conversation), photo(conversation)
    envelhece(velho, 120)

    call_command('prune_attachments', days=90)

    assert not Path(velho.file.path).exists()
    assert Path(novo.file.path).exists()
    assert list(Attachment.objects.all()) == [novo]


def test_retencao_zero_guarda_para_sempre(conversation):
    attachment = photo(conversation)
    envelhece(attachment, 3650)

    call_command('prune_attachments', days=0)

    assert Path(attachment.file.path).exists()


def test_mensagem_sobrevive_ao_anexo_vencido(conversation):
    attachment = photo(conversation, 'mercado, 82 reais')
    envelhece(attachment, 120)

    call_command('prune_attachments', days=90)

    assert Message.objects.get(pk=attachment.message_id).content == 'mercado, 82 reais'
    assert attachments.FORGOTTEN in history(conversation)[0]['content']


def test_arquivo_sem_dono_antigo_e_recolhido(media_root, conversation):
    caminho = orfao(media_root, 'perdido.jpg', horas=ORPHAN_GRACE.total_seconds() / 3600 + 1)

    call_command('prune_attachments')

    assert not caminho.exists()


def test_arquivo_sem_dono_recente_sobrevive(media_root, conversation):
    attachment = photo(conversation)
    caminho = orfao(media_root, 'agora.jpg')

    call_command('prune_attachments')

    assert caminho.exists()
    assert Path(attachment.file.path).exists()


def test_dry_run_nao_apaga(media_root, conversation):
    attachment = photo(conversation)
    envelhece(attachment, 120)
    caminho = orfao(media_root, 'perdido.jpg', horas=48)

    call_command('prune_attachments', days=90, dry_run=True)

    assert Path(attachment.file.path).exists()
    assert caminho.exists()
