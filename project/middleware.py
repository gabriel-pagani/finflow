class PermissionsPolicyMiddleware:
    """Declara quais APIs do navegador a página pode usar.

    O FinFlow não pede câmera, microfone nem localização, então a lista vazia
    nega todas elas. Sem o cabeçalho a permissão fica em aberto: não porque o
    código peça, mas porque nada proíbe. Assim um script injetado tampouco
    alcançaria essas APIs, o que fecha a camada que a CSP não cobre.

    Não há setting nativo no Django para este cabeçalho, diferente do que
    acontece com HSTS e nosniff, daí o middleware próprio.
    """

    POLICY = ', '.join(f'{feature}=()' for feature in [
        'accelerometer',
        'ambient-light-sensor',
        'autoplay',
        'camera',
        'display-capture',
        'encrypted-media',
        'fullscreen',
        'geolocation',
        'gyroscope',
        'magnetometer',
        'microphone',
        'midi',
        'payment',
        'usb',
        'xr-spatial-tracking',
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Permissions-Policy', self.POLICY)
        return response
