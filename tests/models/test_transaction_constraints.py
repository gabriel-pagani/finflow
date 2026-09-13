"""As mesmas regras da transação, agora garantidas pelo banco.

Aqui o save() é chamado direto na instância, sem passar por full_clean(): é o
caminho de um script ou de um shell, que o clean() não cobre.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from app.models import Method, Nature, Transaction, Type


@pytest.mark.parametrize('value', [Decimal('0.00'), Decimal('-10.00')])
def test_valor_nao_positivo_e_recusado(make_transaction, value):
    with pytest.raises(IntegrityError):
        make_transaction(value=value).save()


def test_credito_sem_cartao_e_recusado(make_transaction):
    with pytest.raises(IntegrityError):
        make_transaction(method=Method.CREDIT).save()


def test_cartao_fora_do_credito_e_recusado(make_transaction, card):
    with pytest.raises(IntegrityError):
        make_transaction(card=card).save()


def test_categoria_fora_da_natureza_normal_e_recusada(make_transaction, category):
    with pytest.raises(IntegrityError):
        make_transaction(nature=Nature.INTERNAL, category=category).save()


@pytest.mark.parametrize('campo, valor', [
    ('type', 'TRANSFER'),
    ('method', 'PIX'),
    ('nature', 'REFUND'),
])
def test_valor_fora_das_opcoes_e_recusado(make_transaction, campo, valor):
    with pytest.raises(IntegrityError):
        make_transaction(**{campo: valor}).save()


def test_numero_de_parcela_zerado_e_recusado(make_installment):
    parcelamento = make_installment()
    parcelamento.save()
    primeira = parcelamento.transactions.order_by('parcel').first()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=primeira.pk).update(parcel=0)


def test_data_efetiva_antes_da_transacao_e_recusada(make_transaction):
    transacao = make_transaction()
    transacao.save()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=transacao.pk).update(effective_at=transacao.occurred_at - timedelta(days=1))


def test_interna_no_credito_e_recusada(make_transaction, card):
    with pytest.raises(IntegrityError):
        make_transaction(nature=Nature.INTERNAL, method=Method.CREDIT, card=card).save()


@pytest.mark.parametrize('method', [Method.DEBIT, Method.NOT_APPLICABLE])
def test_interna_avulsa_fora_do_credito_e_aceita(make_transaction, method):
    transacao = make_transaction(nature=Nature.INTERNAL, method=method)
    transacao.save()
    assert transacao.pk


def test_data_efetiva_adiada_fora_do_credito_e_recusada(make_transaction):
    transacao = make_transaction()
    transacao.save()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=transacao.pk).update(effective_at=transacao.occurred_at + timedelta(days=30))
