"""Busca na Pluggy as contas e transações das conexões do MeuPluggy.

Só lê. O que chega vira BankTransaction a revisar; quem lança é o usuário, na
tela de importação.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Method, Type
from .models.bank import BankAccount, BankTransaction


API = 'https://api.pluggy.ai'
KEY_CACHE = 'pluggy:api-key'
# A chave vale 2 h; renovar antes evita que expire no meio de uma busca.
KEY_TIMEOUT = 100 * 60
PARCEL_SUFFIX = re.compile(r'\s+\d+/\d+$')


class PluggyError(Exception):
    pass


def request(path, *, key=None, body=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['X-API-KEY'] = key
    data = json.dumps(body).encode() if body is not None else None

    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, data=data, headers=headers), timeout=20) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise PluggyError(f'Falha ao falar com a Pluggy em {path.split("?")[0]}: {error}') from error


def api_key():
    key = cache.get(KEY_CACHE)
    if not key:
        key = request('/auth', body={'clientId': settings.PLUGGY_CLIENT_ID, 'clientSecret': settings.PLUGGY_CLIENT_SECRET})['apiKey']
        cache.set(KEY_CACHE, key, KEY_TIMEOUT)
    return key


def get(path, params):
    return request(f'{path}?{urllib.parse.urlencode(params)}', key=api_key())


def fetch_transactions(account_id):
    # O /v2 pagina por cursor: o next vem como "?accountId=...&after=...", e só
    # o valor do after volta na próxima chamada.
    params = {'accountId': account_id}
    while True:
        page = get('/v2/transactions', params)
        yield from page['results']
        if not page.get('next'):
            return
        params = {'accountId': account_id, 'after': urllib.parse.parse_qs(urllib.parse.urlsplit(page['next']).query)['after'][0]}


def moment_of(value):
    moment = parse_datetime(value)
    return moment if moment and timezone.is_aware(moment) else None


def local_date(value):
    # As datas vêm em UTC: 14/11 às 01h em Brasília chega como 14/11 04h, mas
    # 13/11 às 22h chegaria como 14/11 01h. O dia que conta é o de Brasília.
    moment = moment_of(value)
    return timezone.localtime(moment).date() if moment else date.fromisoformat(value[:10])


def parse(bank_account, raw):
    """Traduz uma transação da Pluggy para os campos do FinFlow."""
    # O sinal do amount muda com o tipo da conta (na conta corrente a saída é
    # negativa; no cartão, a compra é positiva). O type diz a direção nos dois.
    kind = Type.OUT if raw['type'] == 'DEBIT' else Type.IN
    if kind == Type.IN:
        method = Method.NOT_APPLICABLE
    elif bank_account.kind == BankAccount.Kind.CREDIT:
        method = Method.CREDIT
    else:
        method = Method.DEBIT

    meta = raw.get('creditCardMetadata') or {}
    fields = {
        'type': kind,
        'method': method,
        'value': abs(Decimal(str(raw['amount']))),
        'occurred_at': local_date(raw['date']),
        'moment': moment_of(raw['date']),
        'description': (raw.get('description') or '')[:200],
        'bank_category': (raw.get('category') or '')[:100],
        'card_digits': str(meta.get('cardNumber') or '')[-4:],
        'parcel': None,
        'parcels': None,
        'purchase_date': None,
        'purchase_key': '',
    }

    parcels = meta.get('totalInstallments') or 0
    if parcels > 1 and meta.get('installmentNumber'):
        purchase_date = local_date(meta['purchaseDate']) if meta.get('purchaseDate') else fields['occurred_at']
        base = PARCEL_SUFFIX.sub('', fields['description'])
        fields.update({
            'parcel': meta['installmentNumber'],
            'parcels': parcels,
            'purchase_date': purchase_date,
            'purchase_key': f'{purchase_date.isoformat()}|{parcels}|{base}'[:255],
        })
    return fields


def store(bank_account, raw):
    """Grava ou atualiza a transação; diz se entrou uma nova para revisar."""
    fields = parse(bank_account, raw)
    row = BankTransaction.objects.filter(external_id=raw['id']).first()

    if row:
        # Depois de lançada ou descartada a linha é do usuário: o banco só
        # atualiza o que ainda está para revisar (pendente que virou postada).
        if row.status == BankTransaction.Status.PENDING:
            BankTransaction.objects.filter(pk=row.pk).update(**fields)
        return False

    connection = bank_account.connection
    if (fields['purchase_date'] or fields['occurred_at']) < connection.since:
        return False

    # Parcela nova de uma compra já resolvida segue o destino das irmãs: se a
    # compra virou parcelamento, ela já está nele; se foi descartada, também.
    if fields['purchase_key']:
        twin = (
            BankTransaction.objects
            .filter(bank_account=bank_account, purchase_key=fields['purchase_key'])
            .exclude(status=BankTransaction.Status.PENDING)
            .first()
        )
        if twin:
            fields.update(status=twin.status, installment=twin.installment)

    BankTransaction.objects.create(user=connection.user, bank_account=bank_account, external_id=raw['id'], **fields)
    return fields.get('status', BankTransaction.Status.PENDING) == BankTransaction.Status.PENDING


def sync(connection):
    """Busca as contas e transações da conexão; devolve quantas chegaram para revisar."""
    # ponytail: busca tudo (até 1 ano) a cada vez e confere linha a linha. Para
    # um usuário são poucas centenas; usar o createdAtFrom se pesar.
    new = 0
    for raw_account in get('/accounts', {'itemId': connection.item_id})['results']:
        bank_account, _ = BankAccount.objects.update_or_create(
            external_id=raw_account['id'],
            defaults={
                'connection': connection,
                'kind': raw_account['type'],
                'name': raw_account.get('marketingName') or raw_account.get('name') or '',
            },
        )

        with transaction.atomic():
            seen = set()
            for raw in fetch_transactions(raw_account['id']):
                seen.add(raw['id'])
                new += store(bank_account, raw)

            # Some do banco o que ele desfez (pendente que não se confirmou). Só
            # o que ainda está a revisar sai; o já lançado é do usuário.
            BankTransaction.objects.filter(bank_account=bank_account, status=BankTransaction.Status.PENDING).exclude(external_id__in=seen).delete()

    connection.synced_at = timezone.now()
    connection.save(update_fields=['synced_at'])
    return new
