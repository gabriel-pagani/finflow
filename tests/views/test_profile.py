import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from assistant.models import DailyUsage


def post(client, section, **fields):
    return client.post(reverse('app:profile'), {'section': section, **fields})


def account(**fields):
    return {'first_name': 'Gabriel', 'last_name': 'Pagani', 'username': 'gabriel', 'email': 'gabriel@example.com', **fields}


@pytest.fixture
def allowed(user):
    user.user_permissions.add(Permission.objects.get(codename='use_assistant', content_type__app_label='assistant'))


def test_perfil_exige_login(client):
    assert client.get(reverse('app:profile')).status_code == 302
    assert post(client, 'observations', observations='Nubank').status_code == 302


def test_edita_apenas_observacao_do_usuario(logged, user, other_user, allowed):
    user.observations = 'Uso Itaú'
    user.save(update_fields=['observations'])
    response = logged.get(reverse('app:profile'))
    assert response.status_code == 200
    assert 'Uso Itaú' in response.content.decode()
    response = post(logged, 'observations', observations='Uso Nubank', id=other_user.pk, is_staff=True)
    assert response.status_code == 302
    user.refresh_from_db()
    other_user.refresh_from_db()
    assert user.observations == 'Uso Nubank'
    assert not user.is_staff
    assert other_user.observations == ''
    assert post(logged, 'observations', observations='').status_code == 302
    user.refresh_from_db()
    assert user.observations == ''


def test_observacao_longa_nao_e_gravada(logged, user):
    assert post(logged, 'observations', observations='a' * 2001).status_code == 200
    user.refresh_from_db()
    assert user.observations == ''


def test_edita_dados_da_conta_sem_mexer_no_resto(logged, user):
    user.observations = 'Uso Itaú'
    user.save(update_fields=['observations'])

    assert post(logged, 'account', **account(username='gabriel.pagani', is_staff=True)).status_code == 302

    user.refresh_from_db()
    assert (user.username, user.email, user.first_name) == ('gabriel.pagani', 'gabriel@example.com', 'Gabriel')
    assert user.observations == 'Uso Itaú'
    assert not user.is_staff


@pytest.mark.parametrize('fields', [{'username': 'MARIANA'}, {'email': 'Mariana@example.com'}])
def test_usuario_ou_email_de_outro_e_recusado(logged, user, other_user, fields):
    other_user.email = 'mariana@example.com'
    other_user.save(update_fields=['email'])

    assert post(logged, 'account', **account(**fields)).status_code == 200
    user.refresh_from_db()
    assert user.username == 'gabriel' and not user.email


def test_troca_a_senha_e_continua_logado(logged, user):
    response = post(logged, 'password', old_password='segredo', new_password1='Outra-senha-forte-123', new_password2='Outra-senha-forte-123')
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password('Outra-senha-forte-123')
    assert logged.get(reverse('app:profile')).status_code == 200


def test_senha_atual_errada_nao_troca(logged, user):
    response = post(logged, 'password', old_password='errada', new_password1='Outra-senha-forte-123', new_password2='Outra-senha-forte-123')
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password('segredo')


def test_mostra_os_limites_do_assistente(logged, user, allowed):
    DailyUsage.objects.create(user=user, day=timezone.localdate(), messages=3)
    content = logged.get(reverse('app:profile')).content.decode()
    assert f'3 de {DailyUsage.LIMIT}' in content


def test_sem_acesso_ao_assistente_diz_isso(logged):
    assert 'não tem acesso ao assistente' in logged.get(reverse('app:profile')).content.decode()
