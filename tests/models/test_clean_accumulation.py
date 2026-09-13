"""O clean() junta tudo o que está errado antes de recusar.

Cada checagem é independente das outras, então parar na primeira faria o usuário
descobrir um problema por envio. Estes testes fixam o contrário: dois erros no
mesmo registro chegam juntos, e dois erros no mesmo campo viram duas mensagens.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from app.models import BusinessRule, Card, Method, Nature, Transfer, Type


def test_transferencia_junta_origem_e_destino(user, account, other_account):
    transferencia = Transfer(user=user, origin=account, destination=other_account,
                             value=Decimal('50.00'), occurred_at=date(2026, 9, 4))

    with pytest.raises(ValidationError) as erro:
        transferencia.full_clean()

    assert set(erro.value.error_dict) == {'origin', 'destination'}


def test_transacao_junta_natureza_e_regra_de_negocio(make_transaction, user, account):
    # Cartão criado direto, sem a regra de crédito que o clean dele exigiria:
    # é o que deixa a conta recusar a saída em crédito.
    cartao = Card.objects.create(user=user, account=account, last_digits='1234', closing_day=5, due_day=12)

    with pytest.raises(ValidationError) as erro:
        make_transaction(nature=Nature.INTERNAL, method=Method.CREDIT, card=cartao).full_clean()

    assert sorted(erro.value.message_dict[NON_FIELD_ERRORS]) == sorted([
        f'A conta {account} não permite saída em {Method.CREDIT.label}.',
        f'A natureza {Nature.INTERNAL.label} só mexe no saldo e nunca é em {Method.CREDIT.label}.',
    ])


def test_transacao_junta_as_duas_broncas_do_mesmo_cartao(make_transaction, other_user, other_account, credit_rule):
    BusinessRule.objects.create(account=other_account, type=Type.OUT, method=Method.CREDIT)
    alheio = Card.objects.create(user=other_user, account=other_account, last_digits='5678',
                                 closing_day=5, due_day=12)

    with pytest.raises(ValidationError) as erro:
        make_transaction(method=Method.CREDIT, card=alheio).full_clean()

    assert erro.value.message_dict['card'] == [
        'O cartão escolhido pertence a outra conta.',
        'O cartão escolhido pertence a outro usuário.',
    ]


def test_parcelamento_junta_conta_e_cartao(make_installment, card, other_account):
    with pytest.raises(ValidationError) as erro:
        make_installment(account=other_account, card=card).full_clean()

    assert set(erro.value.error_dict) == {'account', 'card'}


def test_cartao_junta_regra_da_conta_e_travamento(card, other_account, make_transaction):
    make_transaction(card=card, method=Method.CREDIT).save()
    card.account = other_account

    with pytest.raises(ValidationError) as erro:
        card.full_clean()

    assert set(erro.value.error_dict) == {'account', NON_FIELD_ERRORS}
