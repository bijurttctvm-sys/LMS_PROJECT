import math
import time

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import salted_hmac


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _lockout_key(username, client_ip):
    identifier = f'{client_ip}:{(username or "").strip().lower()}'
    digest = salted_hmac('users.login_lockout', identifier).hexdigest()
    return f'users:login-lockout:{digest}'


def is_login_rate_limited(request, username):
    attempt_data = cache.get(_lockout_key(username, get_client_ip(request))) or {}
    locked_until = attempt_data.get('locked_until', 0)
    remaining_seconds = max(0, int(math.ceil(locked_until - time.time())))
    return remaining_seconds > 0, remaining_seconds


def register_failed_login(request, username):
    key = _lockout_key(username, get_client_ip(request))
    attempt_data = cache.get(key) or {'count': 0, 'locked_until': 0}
    attempt_data['count'] += 1

    if attempt_data['count'] >= settings.LOGIN_FAILURE_LIMIT:
        attempt_data['locked_until'] = time.time() + settings.LOGIN_LOCKOUT_SECONDS

    timeout = max(settings.LOGIN_LOCKOUT_SECONDS * 2, 60)
    cache.set(key, attempt_data, timeout=timeout)
    return attempt_data['locked_until']


def clear_failed_logins(request, username):
    cache.delete(_lockout_key(username, get_client_ip(request)))
