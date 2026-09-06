from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str | None:
    real_ip = request.META.get('HTTP_X_REAL_IP')

    if real_ip:
        return real_ip.strip()

    return request.META.get('REMOTE_ADDR')
