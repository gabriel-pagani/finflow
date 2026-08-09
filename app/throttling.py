from django.conf import settings
from django.core.cache import cache


MAX_ATTEMPTS_PER_USER = getattr(settings, 'API_AUTH_MAX_ATTEMPTS_PER_USER')
MAX_ATTEMPTS_PER_IP = getattr(settings, 'API_AUTH_MAX_ATTEMPTS_PER_IP')
LOCKOUT_SECONDS = getattr(settings, 'API_AUTH_LOCKOUT_SECONDS')


def get_client_ip(request):
    return (
        request.headers.get('CF-Connecting-IP', '').strip()
        or request.META.get('REMOTE_ADDR', '')
    )

def _user_key(username):
    return f'api-auth-failures-user:{username.strip().lower()}'

def _ip_key(ip):
    return f'api-auth-failures-ip:{ip}'

def _increment(key):
    if cache.add(key, 1, LOCKOUT_SECONDS):
        return 1

    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, LOCKOUT_SECONDS)
        return 1

def is_locked(username, ip=''):
    if username and cache.get(_user_key(username), 0) >= MAX_ATTEMPTS_PER_USER:
        return True

    return bool(ip) and cache.get(_ip_key(ip), 0) >= MAX_ATTEMPTS_PER_IP

def register_failure(username, ip=''):
    if username:
        _increment(_user_key(username))

    if ip:
        _increment(_ip_key(ip))

def reset(username, ip=''):
    if username:
        cache.delete(_user_key(username))

    if ip:
        cache.delete(_ip_key(ip))
