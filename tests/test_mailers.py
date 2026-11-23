"""
O e-mail sai pelo MAILERS (os EMAIL_* foram descontinuados no Django), lido das
mesmas variáveis EMAIL_* do .env.
"""
import runpy
from pathlib import Path

from django.conf import settings

SETTINGS = Path(settings.BASE_DIR) / 'project' / 'settings.py'
SMTP = 'django.core.mail.backends.smtp.EmailBackend'


def load(monkeypatch, **env):
    for name in ('EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'EMAIL_USE_TLS', 'EMAIL_TIMEOUT'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(str(SETTINGS))


def test_smtp_le_as_variaveis_do_env(monkeypatch):
    loaded = load(monkeypatch, EMAIL_HOST='smtp.exemplo.com', EMAIL_PORT='465', EMAIL_HOST_USER='eu', EMAIL_HOST_PASSWORD='x', EMAIL_USE_TLS='0', EMAIL_TIMEOUT='5')

    assert loaded['MAILERS'] == {'default': {'BACKEND': SMTP, 'OPTIONS': {
        'host': 'smtp.exemplo.com', 'port': 465, 'username': 'eu', 'password': 'x', 'use_tls': False, 'timeout': 5,
    }}}


def test_padrao_e_smtp_com_tls_na_587(monkeypatch):
    options = load(monkeypatch)['MAILERS']['default']['OPTIONS']
    assert (options['port'], options['use_tls'], options['timeout']) == (587, True, 10)


def test_outro_backend_nao_recebe_opcoes_do_smtp(monkeypatch):
    console = 'django.core.mail.backends.console.EmailBackend'
    assert load(monkeypatch, EMAIL_BACKEND=console)['MAILERS'] == {'default': {'BACKEND': console}}


def test_nenhum_ajuste_email_descontinuado(monkeypatch):
    loaded = load(monkeypatch)
    assert not [name for name in loaded if name.startswith('EMAIL_') and name.isupper()]
