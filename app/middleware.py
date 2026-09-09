from django.utils import timezone

from .models import Subscription


class SubscriptionChargesMiddleware:
    SESSION_KEY = 'charges_generated_on'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            # Uma vez por dia por sessão: sem isso a consulta sairia a cada requisição.
            today = timezone.localdate().isoformat()
            if request.session.get(self.SESSION_KEY) != today:
                Subscription.generate_due(user=user)
                request.session[self.SESSION_KEY] = today

        return self.get_response(request)
