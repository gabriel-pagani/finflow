"""As mesmas regras do parcelamento, agora garantidas pelo banco.

Aqui o save() é chamado direto na instância, sem passar por full_clean(): é o
caminho de um script ou de um shell, que o clean() não cobre.
"""
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from app.models import Nature, Transaction


@pytest.mark.parametrize('value', [Decimal('0.00'), Decimal('-10.00')])
def test_valor_nao_positivo_e_recusado(make_installment, value):
    with pytest.raises(IntegrityError):
        make_installment(value=value).save()


@pytest.mark.parametrize('installments', [0, 1, 361])
def test_numero_de_parcelas_fora_da_faixa_e_recusado(make_installment, installments):
    with pytest.raises(IntegrityError):
        make_installment(installments=installments).save()


def test_valor_que_nao_cobre_um_centavo_por_parcela_e_recusado(make_installment):
    with pytest.raises(IntegrityError):
        make_installment(value=Decimal('1.00'), installments=101).save()


def test_parcela_repetida_no_mesmo_parcelamento_e_recusada(make_installment):
    parcelamento = make_installment()
    parcelamento.save()
    ultima = parcelamento.transactions.order_by('parcel').last()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=ultima.pk).update(parcel=1)


def test_numero_de_parcela_sem_parcelamento_e_recusado(make_installment):
    parcelamento = make_installment()
    parcelamento.save()
    primeira = parcelamento.transactions.order_by('parcel').first()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=primeira.pk).update(installment=None)


def test_transacao_avulsa_nao_carrega_parcela(make_transaction):
    transacao = make_transaction()
    transacao.save()
    assert transacao.installment_id is None
    assert transacao.parcel is None


def test_parcela_fora_da_natureza_normal_e_recusada(make_installment):
    parcelamento = make_installment()
    parcelamento.save()
    parcela = parcelamento.transactions.order_by('parcel').first()
    with pytest.raises(IntegrityError):
        Transaction.objects.filter(pk=parcela.pk).update(nature=Nature.INTERNAL)
