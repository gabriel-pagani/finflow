from axes.models import AccessAttempt, AccessFailureLog, AccessLog
from django.apps import apps
from django_otp.admin import OTPAdminSite
from django_otp.plugins.otp_totp.models import TOTPDevice


# O axes e o TOTP não têm tradução: os nomes vão aqui, e as seções deles entram
# na do Sistema.
NAMES = {
    AccessAttempt: ('Tentativa de Acesso', 'Tentativas de Acesso'),
    AccessFailureLog: ('Falha de Acesso', 'Falhas de Acesso'),
    AccessLog: ('Registro de Acesso', 'Registros de Acesso'),
    TOTPDevice: ('Dispositivo 2FA', 'Dispositivos 2FA'),
}
for model, (singular, plural) in NAMES.items():
    model._meta.verbose_name = singular
    model._meta.verbose_name_plural = plural

MOVED = ('axes', 'otp_totp')
for label in MOVED:
    apps.get_app_config(label).verbose_name = apps.get_app_config('app').verbose_name


class AdminSite(OTPAdminSite):
    def _build_app_dict(self, request, label=None):
        if label not in (None, 'app', *MOVED):
            return super()._build_app_dict(request, label)

        app_dict = super()._build_app_dict(request)
        for name in MOVED:
            moved = app_dict.pop(name, None)
            if moved:
                app_dict.setdefault('app', {**moved, 'models': []})['models'] += moved['models']
        if label:
            return {'app': app_dict['app']} if 'app' in app_dict else {}
        return app_dict
