from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

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

    def test_rejects_non_image_profile_picture(self):
        form = ProfileForm(
            data={
                'first_name': 'Test',
                'last_name': 'Instructor',
                'email': 'instructor@example.com',
                'phone': '',
                'google_meet_link': '',
                'preferred_language': User.Language.ENGLISH,
            },
            files={
                'profile_picture': SimpleUploadedFile(
                    'profile.txt',
                    b'not-an-image',
                    content_type='text/plain',
                )
            },
            instance=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('profile_picture', form.errors)


class ChangePasswordViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='student1',
            password='testpass123',
            role=User.Role.STUDENT,
        )

    def test_change_password_updates_password_and_keeps_session_valid(self):
        self.client.login(username='student1', password='testpass123')

        response = self.client.post(
            reverse('change-password'),
            {
                'old_password': 'testpass123',
                'new_password1': 'StrongerPass1!',
                'new_password2': 'StrongerPass1!',
            },
        )

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('StrongerPass1!'))
        profile_response = self.client.get(reverse('profile'))
        self.assertEqual(profile_response.status_code, 200)

    def test_change_password_rejects_incorrect_current_password(self):
        self.client.login(username='student1', password='testpass123')

        response = self.client.post(
            reverse('change-password'),
            {
                'old_password': 'wrong-password',
                'new_password1': 'StrongerPass1!',
                'new_password2': 'StrongerPass1!',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('testpass123'))
        self.assertContains(response, 'Your old password was entered incorrectly')


@override_settings(LOGIN_FAILURE_LIMIT=2, LOGIN_LOCKOUT_SECONDS=60)
class LoginRateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='locked_user',
            password='testpass123',
            role=User.Role.STUDENT,
        )

    def test_login_locks_after_repeated_failures(self):
        login_url = reverse('login')

        self.client.post(login_url, {'username': 'locked_user', 'password': 'bad-password'})
        response = self.client.post(
            login_url,
            {'username': 'locked_user', 'password': 'bad-password'},
        )

        self.assertContains(response, 'Too many failed login attempts')

        blocked_response = self.client.post(
            login_url,
            {'username': 'locked_user', 'password': 'testpass123'},
        )
        self.assertContains(blocked_response, 'Too many failed login attempts')
