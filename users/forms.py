import re

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm

from utils.upload_validators import validate_uploaded_file

from .models import User


def _normalise_email(value):
    return (value or '').strip().lower()


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def clean_username(self):
        return (self.cleaned_data.get('username') or '').strip()

    def clean_email(self):
        email = _normalise_email(self.cleaned_data.get('email'))
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.role = User.Role.STUDENT
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    MEET_CODE_RE = re.compile(r'^[a-z]{3}-[a-z]{4}-[a-z]{3}$', re.IGNORECASE)
    MEET_LINK_RE = re.compile(
        r'^(?:https?://)?meet\.google\.com/(?P<code>[a-z]{3}-[a-z]{4}-[a-z]{3})/?$',
        re.IGNORECASE,
    )

    google_meet_link = forms.CharField(
        required=False,
        help_text='Paste a full Google Meet link, meet.google.com/... link, or just the meet code.',
        widget=forms.TextInput(
            attrs={'placeholder': 'https://meet.google.com/abc-defg-rft or abc-defg-rft'}
        ),
    )

    class Meta:
        model = User
        fields = (
            'first_name',
            'last_name',
            'email',
            'phone',
            'google_meet_link',
            'preferred_language',
            'profile_picture',
        )
        widgets = {
            'preferred_language': forms.Select(choices=User.Language.choices),
            'profile_picture': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
        }

    def clean_email(self):
        email = _normalise_email(self.cleaned_data.get('email'))
        if User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise forms.ValidationError('That email address is already in use.')
        return email

    def clean_google_meet_link(self):
        value = (self.cleaned_data.get('google_meet_link') or '').strip()
        if not value:
            return ''

        compact_value = ''.join(value.split())
        if self.MEET_CODE_RE.fullmatch(compact_value):
            return f'https://meet.google.com/{compact_value.lower()}'

        match = self.MEET_LINK_RE.fullmatch(compact_value)
        if match:
            return f"https://meet.google.com/{match.group('code').lower()}"

        raise forms.ValidationError('Enter a valid Google Meet link or meet code.')

    def clean_profile_picture(self):
        profile_picture = self.cleaned_data.get('profile_picture')
        return validate_uploaded_file(
            profile_picture,
            allowed_extensions={'.jpg', '.jpeg', '.png', '.gif', '.webp'},
            allowed_content_types={'image/*'},
            max_bytes=settings.MAX_PROFILE_IMAGE_BYTES,
            label='Profile picture',
            verify_image=True,
        )


class CreateUserForm(forms.ModelForm):
    role = forms.ChoiceField(
        choices=[
            (User.Role.INSTRUCTOR, 'Trainer'),
            (User.Role.STUDENT, 'Trainee'),
        ],
        help_text='Select the role for this new user.',
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        label='Password',
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        label='Confirm Password',
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'role')

    def clean_username(self):
        return (self.cleaned_data.get('username') or '').strip()

    def clean_email(self):
        email = _normalise_email(self.cleaned_data.get('email'))
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1', '')
        password2 = self.cleaned_data.get('password2', '')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Passwords do not match.')
        password_validation.validate_password(password2, self.instance)
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class ChangePasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].widget.attrs.update({'autocomplete': 'current-password'})
        self.fields['new_password1'].widget.attrs.update({'autocomplete': 'new-password'})
        self.fields['new_password2'].widget.attrs.update({'autocomplete': 'new-password'})
        self.fields['new_password1'].help_text = (
            password_validation.password_validators_help_text_html()
        )
