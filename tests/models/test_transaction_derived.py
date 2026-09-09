"""
A transação derivada de um parcelamento ou de uma transferência não se edita
nem se apaga sozinha: quem manda nela é o objeto que a criou.

O bloqueio vive no delete() da instância. A regeneração da origem e o CASCADE
apagam pelo queryset, que não passa por lá — é o caminho que segue aberto.
"""
import pytest
from django.contrib import admin as django_admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from app.models import Card, Installment, Transaction, Transfer


@pytest.fixture
def admin_request(admin_user):
    request = RequestFactory().get('/')
    request.user = admin_user
    return request


@pytest.fixture
def transaction_admin():
    return django_admin.site.get_model_admin(Transaction)


def test_parcela_e_derivada(make_installment, credit_rule):
    parcelamento = make_installment()
    parcelamento.save()
    assert parcelamento.transactions.first().is_derived


def test_perna_e_derivada(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    assert transferencia.transactions.first().is_derived


def test_transacao_avulsa_nao_e_derivada(make_transaction):
    assert not make_transaction().is_derived


def test_apagar_parcela_sozinha_e_recusado(make_installment, credit_rule):
    parcelamento = make_installment()
    parcelamento.save()
    with pytest.raises(ValidationError):
        parcelamento.transactions.first().delete()


def test_apagar_perna_sozinha_e_recusado(make_transfer):
    transferencia = make_transfer()
    transferencia.save()
    with pytest.raises(ValidationError):
        transferencia.transactions.first().delete()


def test_apagar_transacao_avulsa_e_liberado(make_transaction):
    transacao = make_transaction()
    transacao.save()
    transacao.delete()
    assert Transaction.objects.count() == 0


def test_regenerar_continua_apagando_as_antigas(make_installment, credit_rule):
    parcelamento = make_installment(installments=10)
    parcelamento.save()
    parcelamento.installments = 3
    parcelamento.save()
    assert parcelamento.transactions.count() == 3


def test_apagar_a_origem_continua_levando_as_derivadas(make_installment, make_transfer, credit_rule, user):
    make_installment().save()
    make_transfer().save()
    user.delete()
    assert Transaction.objects.count() == 0
    assert Installment.objects.count() == 0
    assert Transfer.objects.count() == 0
    assert Card.objects.count() == 0


def test_admin_nao_deixa_editar_a_derivada(make_installment, credit_rule, transaction_admin, admin_request):
    parcelamento = make_installment()
    parcelamento.save()
    parcela = parcelamento.transactions.first()
    editaveis = [field.name for field in Transaction._meta.fields
                 if field.name not in transaction_admin.get_readonly_fields(admin_request, parcela)]
    assert editaveis == ['id']


def test_admin_mantem_a_avulsa_editavel(make_transaction, transaction_admin, admin_request):
    transacao = make_transaction()
    transacao.save()
    readonly = transaction_admin.get_readonly_fields(admin_request, transacao)
    assert 'value' not in readonly
    assert 'nature' not in readonly


def test_admin_esconde_a_exclusao_da_derivada(make_installment, credit_rule, transaction_admin, admin_request):
    parcelamento = make_installment()
    parcelamento.save()
    assert not transaction_admin.has_delete_permission(admin_request, parcelamento.transactions.first())


def test_admin_mantem_a_exclusao_da_avulsa(make_transaction, transaction_admin, admin_request):
    transacao = make_transaction()
    transacao.save()
    assert transaction_admin.has_delete_permission(admin_request, transacao)


def test_admin_bloqueia_a_exclusao_em_massa_da_derivada(make_installment, credit_rule, transaction_admin, admin_request):
    parcelamento = make_installment()
    parcelamento.save()
    _, _, perms_needed, _ = transaction_admin.get_deleted_objects(parcelamento.transactions.all(), admin_request)
    assert perms_needed
