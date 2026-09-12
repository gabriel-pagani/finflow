from datetime import date
from decimal import Decimal

from django.urls import reverse

from app.models import Transaction


def test_cria_edita_e_apaga(logged, user, account, category, debit_rule):
    create = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT', 'method': 'DEBIT',
        'nature': 'REGULAR', 'category': category.pk, 'description': 'Mercado', 'value': '25.50',
        'back': reverse('app:transactions_list'),
    })
    assert create.status_code == 302
    transaction = Transaction.objects.get()
    assert transaction.user == user and transaction.effective_at == date(2026, 9, 4)

    update = logged.post(reverse('app:transaction_update', args=[transaction.pk]), {
        'occurred_at': '2026-09-05', 'account': account.pk, 'type': 'OUT', 'method': 'DEBIT',
        'nature': 'REGULAR', 'category': category.pk, 'description': 'Mercado', 'value': '30.00',
    })
    assert update.status_code == 302
    transaction.refresh_from_db()
    assert transaction.value == Decimal('30.00')

    assert logged.post(reverse('app:transaction_delete', args=[transaction.pk])).status_code == 302
    assert not Transaction.objects.exists()


def test_no_credito_a_data_efetiva_e_a_da_fatura(logged, account, card, credit_rule):
    response = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT', 'method': 'CREDIT',
        'card': card.pk, 'nature': 'REGULAR', 'description': 'Crédito', 'value': '80.00',
    })
    assert response.status_code == 302

    transaction = Transaction.objects.get()
    assert transaction.occurred_at == date(2026, 9, 4)
    assert transaction.effective_at == card.charge_date(date(2026, 9, 4))


def test_credito_sem_cartao_volta_com_mensagem(logged, account, credit_rule):
    response = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'OUT', 'method': 'CREDIT',
        'nature': 'REGULAR', 'value': '10.00',
    }, follow=True)

    assert not Transaction.objects.exists()
    assert any('cartão' in str(message).lower() for message in response.context['messages'])


def test_combinacao_fora_das_regras_e_nomeada_na_mensagem(logged, account, debit_rule):
    response = logged.post(reverse('app:transaction_create'), {
        'occurred_at': '2026-09-04', 'account': account.pk, 'type': 'IN', 'method': 'DEBIT',
        'nature': 'REGULAR', 'value': '10.00',
    }, follow=True)

    assert not Transaction.objects.exists()
    assert any(f'A conta {account} não permite entrada em Débito.' == str(message) for message in response.context['messages'])
