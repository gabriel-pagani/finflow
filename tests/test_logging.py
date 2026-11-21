"""
Com DEBUG desligado, os erros do Django aparecem no log do container e chegam por e-mail aos ADMINS.
"""
import logging
from unittest.mock import patch


def test_erro_do_django_chega_no_console_sem_debug(settings):
    assert not settings.DEBUG
    console = next(h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler)
    with patch.object(console, 'emit') as emit:
        logging.getLogger('django.request').error('Internal Server Error: /x')
    emit.assert_called_once()


def test_erro_500_vai_por_email_aos_admins(settings, mailoutbox):
    settings.ADMINS = ['eu@example.com']
    logging.getLogger('django.request').error('Internal Server Error: /x')
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ['eu@example.com']
    assert mailoutbox[0].from_email == settings.DEFAULT_FROM_EMAIL
