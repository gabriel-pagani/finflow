"""Ciclo do cartão: em que data uma compra no crédito cai.

A regra tem duas metades, e é a combinação delas que erra na prática. A
primeira decide a fatura: comprou no dia do fechamento ou depois, a compra vai
para a seguinte; antes disso, fica na atual. A segunda ajusta o calendário, e
só de um lado: o fechamento é sempre o dia cadastrado, mesmo em fim de semana,
enquanto o vencimento que cai em sábado ou domingo anda para a segunda-feira.

As datas destes casos foram escolhidas por serem dias de semana e de fim de
semana reais de 2026, e não por conveniência: um fechamento fixado num sábado
só prova alguma coisa se o dia for mesmo sábado no calendário.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from django.core.exceptions import ValidationError

from app.models import Account, BusinessRule, Card, Installment, Method, Transaction, Type, next_business_day
from app.forms import TransactionForm


pytestmark = pytest.mark.django_db


SAO_PAULO = ZoneInfo('America/Sao_Paulo')


def moment(year, month, day, hour=14, minute=30):
    """Data e hora no fuso do usuário, que é quem decide a fatura."""
    return datetime(year, month, day, hour, minute, tzinfo=SAO_PAULO)


@pytest.fixture
def card(alice, account, business_rules):
    """Cartão da Alice: fecha dia 20 e vence dia 27, os dois no mesmo mês."""
    return Card.objects.create(user=alice, account=account, last_digits='1234', closing_day=20, due_day=27)


# --------------------------------------------------------------------------
# Dia útil
# --------------------------------------------------------------------------

@pytest.mark.parametrize('day, expected', [
    (date(2026, 6, 19), date(2026, 6, 19)),  # sexta: fica onde está
    (date(2026, 6, 20), date(2026, 6, 22)),  # sábado
    (date(2026, 6, 21), date(2026, 6, 22)),  # domingo
    (date(2026, 6, 22), date(2026, 6, 22)),  # segunda
])
def test_next_business_day(day, expected):
    assert next_business_day(day) == expected


def test_only_the_due_date_moves_off_the_weekend(card):
    """As duas datas do ciclo caem no sábado de junho de 2026, e só uma anda.

    Fechamento dia 20 fica no sábado 20, porque encerrar a fatura não depende
    de banco aberto; vencimento dia 27 (sábado) vira segunda 29. Em julho
    nenhuma das duas cai no fim de semana e ambas ficam onde foram
    configuradas — é o que separa o ajuste de um deslocamento fixo.
    """
    assert card.closing_date(2026, 6) == date(2026, 6, 20)
    assert card.due_date(2026, 6) == date(2026, 6, 29)

    assert card.closing_date(2026, 7) == date(2026, 7, 20)
    assert card.due_date(2026, 7) == date(2026, 7, 27)


def test_short_month_clamps_the_day(alice, account, business_rules):
    """Quem fecha dia 31 fecha no último dia do mês que não tem 31."""
    card = Card.objects.create(user=alice, account=account, last_digits='3131', closing_day=31, due_day=10)

    # Fevereiro de 2026 termina no dia 28, um sábado: o fechamento é limitado ao
    # tamanho do mês e para aí, sem escorregar para março.
    assert card.closing_date(2026, 2) == date(2026, 2, 28)


# --------------------------------------------------------------------------
# Qual fatura recebe a compra
# --------------------------------------------------------------------------

def test_purchase_before_closing_goes_to_the_current_invoice(card):
    """Fecha dia 20; comprou no dia 19, vence dia 27 do mesmo mês."""
    assert card.invoice_due_date(date(2026, 5, 19)) == date(2026, 5, 27)


def test_purchase_on_closing_day_goes_to_the_next_invoice(card):
    """O dia do fechamento já é da fatura seguinte: o `>=` da regra."""
    assert card.invoice_due_date(date(2026, 5, 20)) == date(2026, 6, 29)


def test_purchase_after_closing_goes_to_the_next_invoice(card):
    assert card.invoice_due_date(date(2026, 5, 21)) == date(2026, 6, 29)


def test_weekend_closing_still_closes_the_invoice(card):
    """Junho de 2026: o fechamento do dia 20 é sábado e fecha no sábado mesmo.

    Comprar no dia 20 já é comprar depois do fechamento, e a compra vai para a
    fatura de julho. Só a véspera, sexta 19, ainda entra na de junho — que
    vence dia 29, porque aí sim o dia 27 é sábado e o vencimento anda.
    """
    assert card.closing_date(2026, 6) == date(2026, 6, 20)
    assert card.invoice_due_date(date(2026, 6, 19)) == date(2026, 6, 29)
    assert card.invoice_due_date(date(2026, 6, 20)) == date(2026, 7, 27)


def test_due_day_before_closing_day_falls_in_the_next_month(alice, account, business_rules):
    """Fecha dia 25 e vence dia 5: o vencimento é sempre do mês de depois."""
    card = Card.objects.create(user=alice, account=account, last_digits='2505', closing_day=25, due_day=5)

    assert card.invoice_due_date(date(2026, 3, 24)) == date(2026, 4, 6)  # dia 5 é domingo
    assert card.invoice_due_date(date(2026, 3, 25)) == date(2026, 5, 5)


def test_month_end_closing_stays_inside_the_month(alice, account, business_rules):
    """O fechamento de 28/02/2026 é sábado e não escorrega para março.

    Comprar no dia 28 já é da fatura de março, que vence em 10/04; a véspera
    ainda é da de fevereiro, que vence em 10/03. Nenhuma compra de março cai na
    fatura de fevereiro, porque ela fechou antes de o mês virar.
    """
    card = Card.objects.create(user=alice, account=account, last_digits='2828', closing_day=31, due_day=10)

    assert card.closing_date(2026, 2) == date(2026, 2, 28)
    assert card.invoice_due_date(date(2026, 2, 27)) == date(2026, 3, 10)
    assert card.invoice_due_date(date(2026, 2, 28)) == date(2026, 4, 10)


# --------------------------------------------------------------------------
# O que fica gravado na transação
# --------------------------------------------------------------------------

def test_invoice_datetime_keeps_the_informed_time(card):
    """Só o dia muda: a hora da compra é preservada."""
    result = card.invoice_datetime(moment(2026, 5, 19, hour=21, minute=45))

    assert (result.year, result.month, result.day) == (2026, 5, 27)
    assert (result.hour, result.minute) == (21, 45)


def test_transaction_form_stores_the_due_date(alice, account, card, category):
    """A tela recebe a data da compra e grava a data de vencimento."""
    form = TransactionForm(user=alice, data={
        'datetime': '2026-05-19T14:30',
        'account': account.pk,
        'card': card.pk,
        'type': Type.OUT,
        'method': Method.CREDIT,
        'category': category.pk,
        'description': 'Mercado',
        'value': '150.00',
    })

    assert form.is_valid(), form.errors
    transaction = form.save()
    assert transaction.datetime.astimezone(SAO_PAULO).date() == date(2026, 5, 27)


def test_editing_does_not_shift_the_date_again(alice, account, card, category):
    """Na edição a data da tela já é o vencimento, e vale como está.

    Sem esta trava, cada salvamento empurraria a transação uma fatura adiante.
    """
    transaction = Transaction.objects.create(
        user=alice, account=account, card=card, type=Type.OUT, method=Method.CREDIT,
        category=category, description='Mercado', value=Decimal('150.00'),
        datetime=moment(2026, 5, 27),
    )

    form = TransactionForm(user=alice, instance=transaction, data={
        'datetime': '2026-05-27T14:30',
        'account': account.pk,
        'card': card.pk,
        'type': Type.OUT,
        'method': Method.CREDIT,
        'category': category.pk,
        'description': 'Mercado (corrigido)',
        'value': '160.00',
    })

    assert form.is_valid(), form.errors
    transaction = form.save()
    assert transaction.datetime.astimezone(SAO_PAULO).date() == date(2026, 5, 27)


def test_transaction_outside_credit_keeps_the_informed_date(alice, account, category, business_rules):
    """Fora do crédito não há fatura a consultar: a data digitada é a gravada."""
    form = TransactionForm(user=alice, data={
        'datetime': '2026-05-19T14:30',
        'account': account.pk,
        'type': Type.OUT,
        'method': Method.DEBIT,
        'category': category.pk,
        'description': 'Mercado',
        'value': '150.00',
    })

    assert form.is_valid(), form.errors
    assert form.save().datetime.astimezone(SAO_PAULO).date() == date(2026, 5, 19)


# --------------------------------------------------------------------------
# Parcelamento
# --------------------------------------------------------------------------

def test_installment_parcels_land_on_consecutive_due_dates(alice, account, card, category):
    """Cada parcela no vencimento da sua fatura, não somando um mês sobre a anterior.

    A 1ª parcela de uma compra em 19/05 vence em 27/05. A 2ª não vence em 27/06,
    que é sábado, mas em 29/06: o vencimento sai do ciclo de cada fatura, e não
    de arrastar o dia da primeira.
    """
    installment = Installment.objects.create(
        user=alice, account=account, card=card, category=category,
        description='Notebook', value=Decimal('900.00'), installments=3,
        datetime=moment(2026, 5, 19),
    )

    dates = [t.datetime.astimezone(SAO_PAULO).date() for t in installment.transactions.order_by('parcel')]
    assert dates == [date(2026, 5, 27), date(2026, 6, 29), date(2026, 7, 27)]


def test_installment_parcels_carry_the_card(alice, account, card, category):
    installment = Installment.objects.create(
        user=alice, account=account, card=card, category=category,
        description='Notebook', value=Decimal('900.00'), installments=3,
        datetime=moment(2026, 5, 19),
    )

    assert all(t.card_id == card.pk for t in installment.transactions.all())


def test_installment_without_card_keeps_the_current_behaviour(alice, account, category, business_rules):
    """Sem cartão, a data informada é a da 1ª parcela e as demais somam um mês."""
    installment = Installment.objects.create(
        user=alice, account=account, category=category,
        description='Notebook', value=Decimal('900.00'), installments=3,
        datetime=moment(2026, 5, 19),
    )

    dates = [t.datetime.astimezone(SAO_PAULO).date() for t in installment.transactions.order_by('parcel')]
    assert dates == [date(2026, 5, 19), date(2026, 6, 19), date(2026, 7, 19)]


def test_regenerating_parcels_is_idempotent(alice, account, card, category):
    """`datetime` guarda a compra, não o vencimento: regerar dá o mesmo resultado."""
    installment = Installment.objects.create(
        user=alice, account=account, card=card, category=category,
        description='Notebook', value=Decimal('900.00'), installments=3,
        datetime=moment(2026, 5, 19),
    )
    before = [t.datetime for t in installment.transactions.order_by('parcel')]

    installment.generate_transactions()

    assert [t.datetime for t in installment.transactions.order_by('parcel')] == before


# --------------------------------------------------------------------------
# Validação
# --------------------------------------------------------------------------

def test_card_from_another_account_is_rejected(alice, account, destination_account, card, category):
    transaction = Transaction(
        user=alice, account=destination_account, card=card, type=Type.OUT,
        method=Method.CREDIT, category=category, value=Decimal('10.00'),
        datetime=moment(2026, 5, 19),
    )

    with pytest.raises(ValidationError) as error:
        transaction.full_clean()
    assert 'card' in error.value.error_dict


def test_card_outside_credit_is_rejected(alice, account, card, category):
    transaction = Transaction(
        user=alice, account=account, card=card, type=Type.OUT,
        method=Method.DEBIT, category=category, value=Decimal('10.00'),
        datetime=moment(2026, 5, 19),
    )

    with pytest.raises(ValidationError) as error:
        transaction.full_clean()
    assert 'card' in error.value.error_dict


def test_card_needs_a_credit_rule_on_the_account(alice):
    """Conta sem regra de saída em crédito não sustenta um cartão."""
    account = Account.objects.create(description='Conta Sem Credito')
    BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.DEBIT)

    with pytest.raises(ValidationError) as error:
        Card(user=alice, account=account, last_digits='4321', closing_day=20, due_day=27).full_clean()
    assert 'account' in error.value.error_dict


def test_card_from_another_user_is_rejected(alice, bob, account, card, category):
    """O cartão da Alice não entra em transação do Bob, nem por pk direto."""
    transaction = Transaction(
        user=bob, account=account, card=card, type=Type.OUT,
        method=Method.CREDIT, category=category, value=Decimal('10.00'),
        datetime=moment(2026, 5, 19),
    )

    with pytest.raises(ValidationError) as error:
        transaction.full_clean()
    assert 'card' in error.value.error_dict
