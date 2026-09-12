import pytest

from app.models import BusinessRule, Method, Type


@pytest.fixture
def adjustment_rule(account):
    """Saída em não se aplica, que é o que o ajuste de saldo exige."""
    return BusinessRule.objects.create(account=account, type=Type.OUT, method=Method.NOT_APPLICABLE)


@pytest.fixture(autouse=True)
def serving(settings):
    # DEBUG=0 no ambiente de teste liga o SECURE_SSL_REDIRECT, e todo GET viraria 301.
    settings.SECURE_SSL_REDIRECT = False
    # O manifesto do ManifestStaticFilesStorage só existe depois do collectstatic,
    # que roda no contêiner de produção e não aqui.
    settings.STORAGES = {
        **settings.STORAGES,
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client
