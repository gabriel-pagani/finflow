"""Busca no banco as conexões com busca vencida, para a tela Open Finance abrir pronta.

Roda em loop no serviço `scheduler` do compose. O webhook da Pluggy zera o
`synced_at`, então o que o banco avisou entra na volta seguinte.
"""

import logging

from django.core.management.base import BaseCommand

from ...models.bank import BankConnection
from ...pluggy import PluggyError, sync


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Busca as transações das conexões bancárias cuja última busca envelheceu.'

    def handle(self, *args, **options):
        for connection in BankConnection.objects.all():
            if not connection.is_stale:
                continue
            # Uma conexão fora do ar não segura as outras.
            try:
                new = sync(connection)
            except (PluggyError, KeyError):
                logger.exception('Falha ao buscar a conexão %s na Pluggy', connection.pk)
                continue
            if new:
                logger.info('Conexão %s: %s transações novas', connection.pk, new)
