from django.contrib.auth import get_user_model
from django.test import TestCase

from .forms import ProfileForm


User = get_user_model()


class ProfileFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='instructor1',
            password='testpass123',
            role=User.Role.INSTRUCTOR,
            email='instructor@example.com',
        )

    def test_accepts_google_meet_hostname_without_scheme(self):
        form = ProfileForm(
            data={
                'first_name': 'Test',
                'last_name': 'Instructor',
                'email': 'instructor@example.com',
                'phone': '',
                'google_meet_link': 'meet.google.com/abc-defg-rft',
                'preferred_language': User.Language.ENGLISH,
            },
            instance=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['google_meet_link'],
            'https://meet.google.com/abc-defg-rft',
        )

    def test_accepts_plain_google_meet_code(self):
        form = ProfileForm(
            data={
                'first_name': 'Test',
                'last_name': 'Instructor',
                'email': 'instructor@example.com',
                'phone': '',
                'google_meet_link': 'abc-defg-rft',
                'preferred_language': User.Language.ENGLISH,
            },
            instance=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['google_meet_link'],
            'https://meet.google.com/abc-defg-rft',
        )

    def test_accepts_full_google_meet_link(self):
        form = ProfileForm(
            data={
                'first_name': 'Test',
                'last_name': 'Instructor',
                'email': 'instructor@example.com',
                'phone': '',
                'google_meet_link': 'https://meet.google.com/abc-defg-rft',
                'preferred_language': User.Language.ENGLISH,
            },
            instance=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['google_meet_link'],
            'https://meet.google.com/abc-defg-rft',
        )

    def test_rejects_non_google_meet_link(self):
        form = ProfileForm(
            data={
                'first_name': 'Test',
                'last_name': 'Instructor',
                'email': 'instructor@example.com',
                'phone': '',
                'google_meet_link': 'https://example.com/abc-defg-rft',
                'preferred_language': User.Language.ENGLISH,
            },
            instance=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('google_meet_link', form.errors)
