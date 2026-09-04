"""Tira do disco o anexo que já cumpriu o que tinha para cumprir.

A foto de comprovante é lida uma vez, vira lançamento, e a partir daí quem
responde pelo dinheiro é a transação no banco — não a imagem. O áudio é
transcrito na chegada, e o que a conversa relê é o texto. O arquivo continua ali
por um tempo para o usuário conferir um valor que pareça errado; passado esse
tempo, ele é o único pedaço pesado do sistema que ninguém mais abre.

O que expira é só o arquivo. A mensagem fica, a transcrição fica, o lançamento
fica: quem apaga é o `Attachment`, e a mensagem não vai junto porque a cascata
aponta para o outro lado. No chat some a miniatura, e no contexto do modelo a
foto vira o marcador de sempre — o mesmo que ele já recebe para foto antiga.

Não há agendador neste projeto, e um container só para isto seria caro demais
para uma tarefa que roda uma vez por dia. Isto aqui é um comando, e quem o chama
é o cron da máquina, pelo alvo `prune-attachments` do Makefile.
"""

import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import Attachment


# A pasta que a varredura percorre: a primeira parte do caminho que o
# `attachment_path` monta. A varredura não desce a partir do `MEDIA_ROOT` inteiro
# de propósito — um arquivo posto ali por outro motivo não é órfão desta tabela,
# e apagá-lo seria a limpeza destruindo o que não é dela.
FOLDER = 'assistant'

# Entre o arquivo ir para o disco e o INSERT confirmar existe um instante em que
# ele não tem linha nenhuma apontando para ele. Sem esta folga a varredura
# apagaria o comprovante de alguém no meio do envio, e o usuário veria a foto
# sumir da tela sem ter pedido nada.
ORPHAN_GRACE = timedelta(hours=24)


class Command(BaseCommand):
    help = 'Apaga anexos do assistente vencidos e arquivos sem dono no media_root.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Sobrepõe ASSISTANT_ATTACHMENT_RETENTION_DAYS nesta execução. Zero não apaga nada por idade.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Conta o que sairia e não apaga. É como se confere a primeira execução num volume de verdade.',
        )

    def handle(self, *args, **options):
        days = options['days'] if options['days'] is not None else settings.ASSISTANT_ATTACHMENT_RETENTION_DAYS
        dry_run = options['dry_run']

        expired = self.expire(days, dry_run)
        orphans = self.sweep(dry_run)

        verb = 'sairiam' if dry_run else 'saíram'
        self.stdout.write(f'{expired} anexo(s) vencido(s) e {orphans} arquivo(s) sem dono {verb}.')

    def expire(self, days, dry_run):
        """Os anexos velhos demais, um a um.

        Um a um, e não num `delete()` de queryset, porque quem apaga o arquivo do
        disco é o sinal `post_delete` — e apagar em bloco é justamente o caminho
        que o Django otimiza para não instanciar linha nenhuma.
        """
        if days <= 0:
            self.stdout.write('Retenção desligada: nada expira por idade.')
            return 0

        cutoff = timezone.now() - timedelta(days=days)
        expired = Attachment.objects.filter(created__lt=cutoff)

        if dry_run:
            return expired.count()

        count = 0
        for attachment in expired.iterator():
            attachment.delete()
            count += 1

        return count

    def sweep(self, dry_run):
        """O que está no volume sem nenhuma linha apontando para ele.

        Acontece quando a gravação do arquivo passa e a da linha não — o arquivo
        é escrito antes do INSERT, e uma falha entre os dois deixa bytes que
        ninguém mais alcança, nem para ver nem para apagar.
        """
        root = Path(settings.MEDIA_ROOT) / FOLDER

        if not root.is_dir():
            return 0

        known = {
            name.replace('\\', '/')
            for name in Attachment.objects.values_list('file', flat=True)
        }
        deadline = (timezone.now() - ORPHAN_GRACE).timestamp()
        media_root = Path(settings.MEDIA_ROOT)

        count = 0
        for path in root.rglob('*'):
            if not path.is_file():
                continue

            if path.relative_to(media_root).as_posix() in known:
                continue

            if path.stat().st_mtime > deadline:
                continue

            if not dry_run:
                os.remove(path)

            count += 1

        return count
