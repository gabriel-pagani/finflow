"""Unicidade do e-mail do usuário, sem diferenciar maiúsculas."""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

User = get_user_model()


@pytest.fixture
def user_with_email(user):
    user.email = 'gabriel@exemplo.com'
    user.save()
    return user


def test_email_repetido_em_outra_caixa_e_recusado_na_validacao(user_with_email):
    outro = User(username='mariana', password='segredo', email='GABRIEL@EXEMPLO.COM')
    with pytest.raises(ValidationError) as erro:
        outro.full_clean()
    assert 'email' in erro.value.error_dict


def test_email_repetido_em_outra_caixa_e_recusado_no_banco(user_with_email):
    with pytest.raises(IntegrityError):
        User.objects.create_user(username='mariana', password='segredo',
                                 email='GABRIEL@EXEMPLO.COM')


def test_varios_usuarios_sem_email_convivem(db):
    User.objects.create_user(username='ana', password='segredo')
    User.objects.create_user(username='bruno', password='segredo')
    assert User.objects.count() == 2


def test_email_vazio_vira_nulo_no_clean(db):
    usuario = User(username='ana', password='segredo', email='')
    usuario.full_clean()
    assert usuario.email is None
