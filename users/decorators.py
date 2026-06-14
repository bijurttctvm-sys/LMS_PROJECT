from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .models import User


def role_home(user):
    if not getattr(user, 'is_authenticated', False):
        return 'login'
    if user.role == User.Role.ADMIN:
        return 'admin-dashboard'
    if user.role == User.Role.INSTRUCTOR:
        return 'instructor-dashboard'
    return 'student-dashboard'


def role_required(*roles, message='You do not have permission to view that page.'):
    """Require an authenticated user with one of the allowed roles."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if roles and request.user.role not in roles:
                if message:
                    messages.error(request, message)
                return redirect(role_home(request.user))
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
