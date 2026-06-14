import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


UPPERCASE_RE = re.compile(r'[A-Z]')
LOWERCASE_RE = re.compile(r'[a-z]')
DIGIT_RE = re.compile(r'\d')
SPECIAL_RE = re.compile(r'[^A-Za-z0-9]')


class StrongPasswordValidator:
    """Enforce the LMS password complexity rules."""

    def validate(self, password, user=None):
        errors = []

        if len(password) < 8:
            errors.append(_('at least 8 characters'))
        if not UPPERCASE_RE.search(password):
            errors.append(_('at least one uppercase letter'))
        if not LOWERCASE_RE.search(password):
            errors.append(_('at least one lowercase letter'))
        if not DIGIT_RE.search(password):
            errors.append(_('at least one number'))
        if not SPECIAL_RE.search(password):
            errors.append(_('at least one special character'))

        if errors:
            raise ValidationError(
                _('Password must contain %(rules)s.'),
                code='password_too_weak',
                params={'rules': ', '.join(errors)},
            )

    def get_help_text(self):
        return _(
            'Your password must be at least 8 characters long and include '
            'uppercase, lowercase, numeric, and special characters.'
        )
