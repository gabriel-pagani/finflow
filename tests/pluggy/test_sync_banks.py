from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from app.management.commands import sync_banks
from app.models import BankConnection
from app.pluggy import PluggyError


@pytest.fixture
def syncs(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_banks, 'sync', lambda connection: calls.append(connection.item_id) or 0)
    return calls


def test_busca_so_as_conexoes_vencidas(user, syncs):
    now = timezone.now()
    BankConnection.objects.create(user=user, item_id='recente', synced_at=now)
    BankConnection.objects.create(user=user, item_id='velha', synced_at=now - BankConnection.STALE_AFTER - timedelta(seconds=1))
    # O webhook zera o synced_at quando o banco tem novidade.
    BankConnection.objects.create(user=user, item_id='avisada', synced_at=None)

    call_command('sync_banks')

    assert sorted(syncs) == ['avisada', 'velha']


def test_conexao_com_falha_nao_segura_as_outras(user, monkeypatch, caplog):
    calls = []

    def sync(connection):
        calls.append(connection.item_id)
        if connection.item_id == 'quebrada':
            raise PluggyError('fora do ar')
        return 0

    monkeypatch.setattr(sync_banks, 'sync', sync)
    BankConnection.objects.create(user=user, item_id='quebrada')
    BankConnection.objects.create(user=user, item_id='boa')

    call_command('sync_banks')

    assert sorted(calls) == ['boa', 'quebrada']
    assert 'Falha ao buscar a conexão' in caplog.text
