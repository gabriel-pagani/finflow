from django.contrib.admin.apps import AdminConfig


class OTPAdminConfig(AdminConfig):
    """Faz o portal de administração exigir um segundo fator (TOTP) no login.

    A troca é feita por default_site, e não reatribuindo admin.site.__class__ em
    urls.py: o autodiscover instancia o site antes de as URLs serem carregadas,
    então a reatribuição tardia não alcançaria a view de login já resolvida.

    Fica em project/ e não em app/apps.py porque o Django recusa um módulo de
    apps com mais de uma AppConfig candidata a padrão.
    """

    default_site = 'django_otp.admin.OTPAdminSite'
