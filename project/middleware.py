from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from app.utils.otp import confirmed_device


class RequireOTPMiddleware:
    """
    O segundo fator vale para todo mundo, e não só para o portal.

    Sessão sem código conferido não anda no sistema: quem ainda não cadastrou o
    aplicativo vai para o cadastro, e quem já cadastrou não tem como ter chegado
    aqui sem passar pelo código — a não ser numa sessão aberta antes disto
    existir, que não vale e é encerrada.
    """

    def __init__(self, get_response):
        self.get_response = get_response

        self.setup_path = reverse('app:otp_setup')
        # A entrada, a saída e o pedido de conta ficam abertos, senão não há
        # como entrar nem sair da tela de cadastro.
        self.open_paths = {
            self.setup_path, reverse('app:login'), reverse('app:login_token'),
            reverse('app:logout'), reverse('app:access_request'),
        }
        # O portal tem o segundo fator dele, no próprio login; capturá-lo aqui
        # mandaria o administrador para a tela errada.
        self.open_prefixes = (f'/{settings.ADMIN_PANEL_PATH.strip("/")}/', settings.STATIC_URL, settings.MEDIA_URL)

    def __call__(self, request):
        user = getattr(request, 'user', None)

        if user is not None and user.is_authenticated and not user.is_verified() and not self.is_open(request.path):
            if confirmed_device(user):
                logout(request)
                return redirect('app:login')
            return redirect('app:otp_setup')

        return self.get_response(request)

    def is_open(self, path):
        return path in self.open_paths or path.startswith(self.open_prefixes)


class PermissionsPolicyMiddleware:
    FEATURES = {
        'accelerometer': '()',
        'autoplay': '()',
        'camera': '(self)',
        'display-capture': '()',
        'encrypted-media': '()',
        'fullscreen': '()',
        'geolocation': '()',
        'gyroscope': '()',
        'magnetometer': '()',
        'microphone': '(self)',
        'midi': '()',
        'payment': '()',
        'usb': '()',
        'xr-spatial-tracking': '()',
    }

    POLICY = ', '.join(f'{feature}={origins}' for feature, origins in FEATURES.items())

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Permissions-Policy', self.POLICY)
        return response
