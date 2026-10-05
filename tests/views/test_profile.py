from django.urls import reverse


def test_perfil_exige_login(client):
    assert client.get(reverse('app:profile')).status_code == 302
    assert client.post(reverse('app:profile'), {'observations': 'Nubank'}).status_code == 302


def test_edita_apenas_observacao_do_usuario(logged, user, other_user):
    user.observations = 'Uso Itaú'
    user.save(update_fields=['observations'])
    response = logged.get(reverse('app:profile'))
    assert response.status_code == 200
    assert 'Uso Itaú' in response.content.decode()
    response = logged.post(reverse('app:profile'), {
        'observations': 'Uso Nubank', 'id': other_user.pk, 'is_staff': True,
    })
    assert response.status_code == 302
    user.refresh_from_db()
    other_user.refresh_from_db()
    assert user.observations == 'Uso Nubank'
    assert not user.is_staff
    assert other_user.observations == ''
    assert logged.post(reverse('app:profile'), {'observations': ''}).status_code == 302
    user.refresh_from_db()
    assert user.observations == ''


def test_observacao_longa_nao_e_gravada(logged, user):
    assert logged.post(reverse('app:profile'), {'observations': 'a' * 2001}).status_code == 200
    user.refresh_from_db()
    assert user.observations == ''
