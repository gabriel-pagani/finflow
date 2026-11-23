from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from app.models import BusinessRule, Card, Method, Type


@pytest.fixture
def limited(card):
    Card.objects.filter(pk=card.pk).update(limit=Decimal('1000.00'))
    card.refresh_from_db()
    return card


def used_of(client, card):
    return next(item.used for item in client.get(reverse('app:cards_list')).context['object_list'] if item.pk == card.pk)


def test_soma_so_o_que_ainda_vai_vencer(logged, limited, account, other_account_card, make_transaction):
    BusinessRule.objects.create(account=account, type=Type.IN, method=Method.CREDIT)
    today = timezone.localdate()
    credit = {'card': limited, 'method': Method.CREDIT, 'occurred_at': today}

    make_transaction(value=Decimal('300.00'), **credit).save()
    make_transaction(value=Decimal('50.00'), type=Type.IN, **credit).save()  # estorno devolve
    make_transaction(value=Decimal('200.00'), **{**credit, 'occurred_at': today - timedelta(days=120)}).save()  # fatura já vencida
    make_transaction(value=Decimal('70.00')).save()  # débito não usa limite

    assert used_of(logged, limited) == Decimal('250.00')
    assert used_of(logged, other_account_card) == 0


def test_parcelas_futuras_contam(logged, limited, make_transaction):
    first = make_transaction(value=Decimal('100.00'), card=limited, method=Method.CREDIT, occurred_at=timezone.localdate())
    first.save()
    later = make_transaction(value=Decimal('100.00'), card=limited, method=Method.CREDIT, occurred_at=timezone.localdate() + timedelta(days=62))
    later.save()

    assert used_of(logged, limited) == Decimal('200.00')


def test_barra_so_aparece_com_limite(logged, card, make_transaction):
    assert 'credit-card-limit' not in logged.get(reverse('app:cards_list')).content.decode()

    Card.objects.filter(pk=card.pk).update(limit=Decimal('1000.00'))
    make_transaction(value=Decimal('250.00'), card=card, method=Method.CREDIT, occurred_at=timezone.localdate()).save()
    html = logged.get(reverse('app:cards_list')).content.decode()

    assert '250,00 de 1.000,00' in html
    assert 'style="width: 25%"' in html


def test_limite_e_salvo_pelo_formulario(logged, account, credit_rule):
    logged.post(reverse('app:card_create'), {
        'account': account.pk, 'last_digits': '4321', 'closing_day': '5', 'due_day': '12', 'limit': '1500.00',
    })
    assert Card.objects.get().limit == Decimal('1500.00')


def test_limite_zero_e_recusado(card):
    with pytest.raises(IntegrityError):
        Card.objects.filter(pk=card.pk).update(limit=0)


def test_soma_nao_desfaz_a_ordem_dos_cartoes(logged, user, account, other_account, card, other_account_card):
    cards = list(logged.get(reverse('app:cards_list')).context['object_list'])
    assert cards == sorted(cards, key=lambda item: (item.account.description, item.last_digits))
    assert logged.get(reverse('app:cards_list')).context['object_list'].query.order_by
