from django.urls import reverse

from app.forms.finance import InstallmentForm, TransactionForm
from app.models import Card


def test_cria_edita_e_apaga(logged, account, credit_rule):
    create = logged.post(reverse('app:card_create'), {
        'account': account.pk, 'last_digits': '4321', 'closing_day': '5', 'due_day': '12',
    })
    assert create.status_code == 302
    card = Card.objects.get()

    update = logged.post(reverse('app:card_update', args=[card.pk]), {
        'account': account.pk, 'last_digits': '4321', 'closing_day': '6', 'due_day': '15',
    })
    assert update.status_code == 302
    card.refresh_from_db()
    assert (card.closing_day, card.due_day) == (6, 15)

    assert logged.post(reverse('app:card_delete', args=[card.pk])).status_code == 302
    assert not Card.objects.exists()


def test_cartao_em_uso_nao_e_removido(logged, card, credit_rule, make_transaction):
    make_transaction(card=card, method='CREDIT').save()

    response = logged.post(reverse('app:card_delete', args=[card.pk]), follow=True)

    assert Card.objects.filter(pk=card.pk).exists()
    assert any('não pode ser removido' in str(message) for message in response.context['messages'])


def test_cartao_repetido_e_recusado(logged, account, card, credit_rule):
    response = logged.post(reverse('app:card_create'), {
        'account': account.pk, 'last_digits': card.last_digits, 'closing_day': '5', 'due_day': '12',
    }, follow=True)

    assert Card.objects.count() == 1
    assert any('Esse cartão já foi cadastrado.' == str(message) for message in response.context['messages'])


def test_segundo_cartao_principal_e_recusado(logged, account, card, credit_rule):
    Card.objects.filter(pk=card.pk).update(is_main=True)

    response = logged.post(reverse('app:card_create'), {
        'account': account.pk, 'last_digits': '4321', 'closing_day': '5', 'due_day': '12', 'is_main': 'on',
    }, follow=True)

    assert Card.objects.count() == 1
    assert any('Já existe um cartão principal' in str(message) for message in response.context['messages'])


def test_cartao_cancelado_nao_pode_ser_principal(logged, account, credit_rule):
    response = logged.post(reverse('app:card_create'), {
        'account': account.pk, 'last_digits': '4321', 'closing_day': '5', 'due_day': '12',
        'is_main': 'on', 'is_cancelled': 'on',
    }, follow=True)

    assert not Card.objects.exists()
    assert any('cancelado não pode ser o principal' in str(message) for message in response.context['messages'])


def test_cartao_cancelado_sai_das_opcoes_mas_fica_na_compra_que_o_usa(user, card, make_transaction):
    compra = make_transaction(card=card, method='CREDIT')
    compra.save()
    Card.objects.filter(pk=card.pk).update(is_cancelled=True)

    assert card not in TransactionForm(user=user).fields['card'].queryset
    assert card not in InstallmentForm(user=user).fields['card'].queryset
    assert card in TransactionForm(user=user, instance=compra).fields['card'].queryset
