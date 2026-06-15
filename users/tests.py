from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from courses.models import Course, Enrollment

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


class PublicRegistrationTests(TestCase):
    def test_register_page_shows_password_rules(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Your password must be at least 8 characters long and include uppercase, lowercase, numeric, and special characters.',
        )

    def test_public_registration_creates_student_and_logs_them_in(self):
        response = self.client.post(
            reverse('register'),
            {
                'username': 'new_trainee',
                'email': 'new_trainee@example.com',
                'password1': 'StrongPass123!',
                'password2': 'StrongPass123!',
            },
        )

        self.assertRedirects(response, reverse('course-list'))
        created_user = User.objects.get(username='new_trainee')
        self.assertEqual(created_user.role, User.Role.STUDENT)
        dashboard_response = self.client.get(reverse('course-list'))
        self.assertEqual(dashboard_response.status_code, 200)


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


class AdminUserManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_manager',
            password='AdminPass123!',
            role=User.Role.ADMIN,
        )
        self.trainer = User.objects.create_user(
            username='trainer_manager',
            password='TrainerPass123!',
            role=User.Role.INSTRUCTOR,
            email='trainer@example.com',
        )
        self.student = User.objects.create_user(
            username='student_manager',
            password='StudentPass123!',
            role=User.Role.STUDENT,
            email='student@example.com',
        )
        self.client.force_login(self.admin)

    def test_admin_can_open_trainee_management_page(self):
        response = self.client.get(reverse('manage-users', args=[User.Role.STUDENT]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Manage Trainees')
        self.assertContains(response, 'student_manager')
        self.assertNotContains(response, 'trainer_manager')

    def test_delete_student_with_related_records_deactivates_instead_of_deleting(self):
        course = Course.objects.create(
            title='Protected Student Course',
            instructor=self.trainer,
            is_active=True,
        )
        Enrollment.objects.create(
            student=self.student,
            course=course,
            is_active=True,
        )

        response = self.client.post(
            reverse('delete-user', args=[self.student.pk]),
            {'next': reverse('manage-users', args=[User.Role.STUDENT])},
        )

        self.assertRedirects(response, reverse('manage-users', args=[User.Role.STUDENT]))
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())

    def test_delete_trainer_without_related_records_removes_account(self):
        response = self.client.post(
            reverse('delete-user', args=[self.trainer.pk]),
            {'next': reverse('manage-users', args=[User.Role.INSTRUCTOR])},
        )

        self.assertRedirects(response, reverse('manage-users', args=[User.Role.INSTRUCTOR]))
        self.assertFalse(User.objects.filter(pk=self.trainer.pk).exists())

    def test_toggle_user_active_view_updates_status(self):
        response = self.client.post(
            reverse('toggle-user-active', args=[self.trainer.pk]),
            {'next': reverse('manage-users', args=[User.Role.INSTRUCTOR])},
        )

        self.assertRedirects(response, reverse('manage-users', args=[User.Role.INSTRUCTOR]))
        self.trainer.refresh_from_db()
        self.assertFalse(self.trainer.is_active)

    def test_admin_can_create_student_without_email(self):
        response = self.client.post(
            f"{reverse('create-user')}?role={User.Role.STUDENT}",
            {
                'role': User.Role.STUDENT,
                'username': 'new_student',
                'first_name': 'New',
                'last_name': 'Student',
                'email': '',
                'password1': 'StrongPass123!',
                'password2': 'StrongPass123!',
            },
        )

        self.assertRedirects(response, reverse('manage-users', args=[User.Role.STUDENT]))
        created_user = User.objects.get(username='new_student')
        self.assertEqual(created_user.role, User.Role.STUDENT)
        self.assertEqual(created_user.email, '')

    def test_create_user_view_shows_duplicate_email_error(self):
        response = self.client.post(
            f"{reverse('create-user')}?role={User.Role.INSTRUCTOR}",
            {
                'role': User.Role.INSTRUCTOR,
                'username': 'duplicate_email_trainer',
                'first_name': 'Duplicate',
                'last_name': 'Trainer',
                'email': 'trainer@example.com',
                'password1': 'StrongPass123!',
                'password2': 'StrongPass123!',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'An account with this email already exists.')


class SuperuserRoleTests(TestCase):
    def test_create_superuser_sets_admin_role(self):
        superuser = User.objects.create_superuser(
            username='siteadmin',
            email='siteadmin@example.com',
            password='StrongPass123!',
        )

        self.assertTrue(superuser.is_superuser)
        self.assertEqual(superuser.role, User.Role.ADMIN)

    def test_saving_superuser_normalises_role_to_admin(self):
        user = User.objects.create_user(
            username='promoted-user',
            password='StrongPass123!',
            role=User.Role.STUDENT,
        )

        user.is_staff = True
        user.is_superuser = True
        user.save()
        user.refresh_from_db()

        self.assertEqual(user.role, User.Role.ADMIN)


class PublicSeoTests(TestCase):
    def test_home_page_renders_indexable_marketing_landing_page(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'AI-powered multilingual learning management system for structured training programs.',
        )
        self.assertContains(
            response,
            '<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">',
            html=True,
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="http://testserver/">',
            html=True,
        )
        self.assertContains(response, '"@type": "SoftwareApplication"')
        self.assertContains(response, 'Advanced SEO plus product clarity for a discoverable LMS')

    def test_authenticated_home_redirects_to_role_dashboard(self):
        student = User.objects.create_user(
            username='seo-student',
            password='StrongPass123!',
            role=User.Role.STUDENT,
        )
        self.client.force_login(student)

        response = self.client.get(reverse('home'))

        self.assertRedirects(response, reverse('student-dashboard'))

    def test_login_page_uses_noindex_follow(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<meta name="robots" content="noindex,follow">',
            html=True,
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="http://testserver/users/login/">',
            html=True,
        )

    def test_robots_txt_points_to_sitemap_and_blocks_private_routes(self):
        response = self.client.get(reverse('robots-txt'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertContains(response, 'Sitemap: http://testserver/sitemap.xml')
        self.assertContains(response, 'Disallow: /student-dashboard/')
        self.assertContains(response, 'Disallow: /users/')

    def test_sitemap_xml_lists_home_page(self):
        response = self.client.get(reverse('sitemap-xml'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertContains(response, '<loc>http://testserver/</loc>')
        self.assertContains(response, '<changefreq>weekly</changefreq>')
