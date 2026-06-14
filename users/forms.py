from django import forms
from django.contrib.auth.forms import UserCreationForm
import re

from .models import User


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

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
        }

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

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1', '')
        p2 = cleaned.get('password2', '')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user
