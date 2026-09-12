import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from assistant.models import Attachment


# Só a pasta do assistente é varrida: arquivo posto no media_root por outro
# motivo não é órfão desta tabela.
FOLDER = 'assistant'

# O arquivo vai para o disco antes do INSERT. Sem folga, a varredura apagaria o
# comprovante de alguém no meio do envio.
ORPHAN_GRACE = timedelta(hours=24)


class Command(BaseCommand):
    help = 'Apaga os anexos do assistente vencidos e os arquivos sem dono na pasta dele.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None, help='Sobrepõe ASSISTANT_ATTACHMENT_RETENTION_DAYS. Zero não apaga nada por idade.')
        parser.add_argument('--dry-run', action='store_true', help='Conta o que sairia sem apagar.')

    def handle(self, *args, **options):
        days = options['days'] if options['days'] is not None else settings.ASSISTANT_ATTACHMENT_RETENTION_DAYS
        dry_run = options['dry_run']

        expired = self.expire(days, dry_run)
        orphans = self.sweep(dry_run)

        verb = 'sairiam' if dry_run else 'saíram'
        self.stdout.write(f'{expired} anexo(s) vencido(s) e {orphans} arquivo(s) sem dono {verb}.')

    def expire(self, days, dry_run):
        if days <= 0:
            return 0

        expired = Attachment.objects.filter(created_at__lt=timezone.now() - timedelta(days=days))
        if dry_run:
            return expired.count()

        # Um a um, e não pelo queryset: quem tira o arquivo do disco é o
        # post_delete, que o delete em bloco não dispara por instância.
        count = 0
        for attachment in expired.iterator():
            attachment.delete()
            count += 1
        return count

    def sweep(self, dry_run):
        media_root = Path(settings.MEDIA_ROOT)
        root = media_root / FOLDER
        if not root.is_dir():
            return 0

        known = set(Attachment.objects.values_list('file', flat=True))
        deadline = (timezone.now() - ORPHAN_GRACE).timestamp()

        count = 0
        for path in root.rglob('*'):
            if not path.is_file() or path.relative_to(media_root).as_posix() in known or path.stat().st_mtime > deadline:
                continue
            if not dry_run:
                os.remove(path)
            count += 1
        return count
