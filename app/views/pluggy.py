import hashlib
import hmac
import time

from django.conf import settings
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt


# A Pluggy recusa assinatura com mais de 5 minutos de diferença; a mesma folga
# aqui impede que uma entrega capturada seja reenviada depois.
SIGNATURE_TOLERANCE = 300


def valid_signature(header, body, secret, now):
    """Confere o X-Pluggy-Signature (t=<unix>,v1=<hmac-sha256 de "t.corpo">)."""
    if not secret or not header:
        return False

    parts = dict(item.split('=', 1) for item in header.split(',') if '=' in item)
    timestamp, signature = parts.get('t', ''), parts.get('v1', '')
    if not timestamp.isdigit() or abs(now - int(timestamp)) > SIGNATURE_TOLERANCE:
        return False

    expected = hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# Quem chama é a Pluggy, sem sessão nem token de CSRF: a origem é provada pela
# assinatura. Os avisos são só um sinal e, por enquanto, não são processados.
@method_decorator(csrf_exempt, name='dispatch')
class PluggyWebhookView(View):
    http_method_names = ['post']

    def post(self, request):
        header = request.headers.get('X-Pluggy-Signature', '')
        if not valid_signature(header, request.body, settings.PLUGGY_WEBHOOK_SECRET, int(time.time())):
            return HttpResponse(status=401)
        return HttpResponse(status=200)
