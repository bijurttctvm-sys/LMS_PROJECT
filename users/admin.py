from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom User model."""

    list_display = (
        'username',
        'email',
        'role',
        'preferred_language',
        'is_staff',
        'is_active',
    )
    list_filter = ('role', 'preferred_language', 'is_staff', 'is_superuser', 'is_active')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'phone')

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            _('LMS profile'),
            {
                'fields': (
                    'role',
                    'google_meet_link',
                    'profile_picture',
                    'phone',
                    'preferred_language',
                )
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            _('LMS profile'),
            {
                'fields': (
                    'role',
                    'preferred_language',
                )
            },
        ),
    )
