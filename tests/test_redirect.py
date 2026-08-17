"""O 'back' dos formulários não leva o usuário para fora do site.

As telas de escrita devolvem o usuário para a página que ele estava vendo, e o
destino chega no próprio POST. Sem validação, esse campo viraria redirect
aberto: um link montado por terceiro levaria o usuário logado a um domínio de
fora logo depois de uma ação legítima, que é o passo de que o phishing precisa.

Vive fora do arquivo de autorização de propósito — aqui não se verifica quem
alcança o quê, e sim para onde a resposta aponta depois.
"""

from django.urls import reverse
import pytest


pytestmark = pytest.mark.django_db


def create_payload(account, category, **overrides):
    data = {
        'account': account.pk,
        'category': category.pk,
        'type': 'OUT',
        'method': 'DEBIT',
        'description': 'Teste',
        'value': '10.00',
        'datetime': '2026-01-01T10:00',
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize('destino', [
    'https://evil.com/phish',
    '//evil.com',
    'http://evil.com',
    'https://evil.com\\@example.com',
])
def test_back_externo_e_descartado(alice_logged, account, category, business_rules, destino):
    response = alice_logged.post(
        reverse('app:transaction_create'),
        create_payload(account, category, back=destino),
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('app:transactions_list')


def test_back_interno_e_preservado(alice_logged, account, category, business_rules):
    interno = f"{reverse('app:transactions_list')}?page=2"

    response = alice_logged.post(
        reverse('app:transaction_create'),
        create_payload(account, category, back=interno),
    )

    assert response['Location'] == interno


def test_back_externo_tambem_e_descartado_no_erro(alice_logged, account, category, business_rules):
    """O caminho do formulário inválido tem get_success_url próprio: ele redireciona
    para a mesma origem e precisa da mesma validação."""
    response = alice_logged.post(
        reverse('app:transaction_create'),
        create_payload(account, category, back='https://evil.com/phish', value='0.00'),
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('app:transactions_list')
