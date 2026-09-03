class PermissionsPolicyMiddleware:
    """Declara quais APIs do navegador a página pode usar.

    A lista vazia nega a API por completo, e é o que vale para quase todas: o
    FinFlow não pede câmera, localização nem sensor nenhum. Sem o cabeçalho a
    permissão fica em aberto — não porque o código peça, mas porque nada proíbe
    —, e um script injetado alcançaria essas APIs. É a camada que a CSP não
    cobre.

    A exceção é o microfone, e a exceção é o assistente: o chat grava o áudio
    que vira lançamento. `(self)` libera a própria origem e só ela, então um
    iframe de terceiro continua sem alcance.

    A câmera segue negada, mesmo o chat aceitando foto. A foto entra por seletor
    de arquivo, e quem abre a câmera do celular ali é o sistema operacional,
    fora do alcance desta política — o site nunca chama getUserMedia com vídeo.

    A lista traz só o que os navegadores de hoje reconhecem. Negar um nome que
    eles não conhecem não protege de nada — a API não existe para ser usada — e
    rende um erro no console a cada carregamento de página, que atrapalha quem
    for depurar qualquer outra coisa ali.

    Não há setting nativo no Django para este cabeçalho, diferente do que
    acontece com HSTS e nosniff, daí o middleware próprio.
    """

    FEATURES = {
        'accelerometer': '()',
        'autoplay': '()',
        'camera': '()',
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
