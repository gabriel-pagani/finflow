from django.urls import reverse

from app.models import Installment, Transaction, Transfer


def test_parcelamento_gera_as_parcelas(logged, account, card, credit_rule):
    response = logged.post(reverse('app:installment_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'card': card.pk,
        'description': 'Geladeira', 'value': '300.00', 'installments': '3',
    })

    assert response.status_code == 302
    assert Transaction.objects.count() == 3


def test_parcela_nao_se_edita_e_apaga_o_parcelamento_inteiro(logged, account, card, credit_rule):
    logged.post(reverse('app:installment_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'card': card.pk,
        'description': 'Geladeira', 'value': '300.00', 'installments': '3',
    })
    parcel = Transaction.objects.first()

    assert logged.post(reverse('app:transaction_update', args=[parcel.pk]), {}).status_code == 403

    assert logged.post(reverse('app:transaction_delete', args=[parcel.pk])).status_code == 302
    assert not Installment.objects.exists() and not Transaction.objects.exists()


def test_transferencia_gera_as_duas_pernas(logged, account, other_account, transfer_out_rule, transfer_in_rule):
    response = logged.post(reverse('app:transfer_create'), {
        'occurred_at': '2026-09-04', 'origin': account.pk, 'destination': other_account.pk,
        'description': 'Ajuste', 'value': '150.00',
    })

    assert response.status_code == 302
    assert Transaction.objects.count() == 2


def test_perna_apaga_a_transferencia_inteira(logged, account, other_account, transfer_out_rule, transfer_in_rule):
    logged.post(reverse('app:transfer_create'), {
        'occurred_at': '2026-09-04', 'origin': account.pk, 'destination': other_account.pk,
        'description': 'Ajuste', 'value': '150.00',
    })
    leg = Transaction.objects.first()

    assert logged.post(reverse('app:transaction_delete', args=[leg.pk])).status_code == 302
    assert not Transfer.objects.exists() and not Transaction.objects.exists()
