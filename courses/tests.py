from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from doubt_sessions.models import DoubtSession
from videos.models import Video

from .models import Course, Enrollment


User = get_user_model()


class CourseDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_user',
            password='pass123',
            role=User.Role.ADMIN,
        )
        self.instructor = User.objects.create_user(
            username='instructor_user',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.student = User.objects.create_user(
            username='student_user',
            password='pass123',
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            title='Physics 101',
            instructor=self.instructor,
        )
        self.video = Video.objects.create(
            course=self.course,
            title='Lesson 1',
            video_key='videos/1/demo.mp4',
            english_pdf_key='pdfs/1/demo_en.pdf',
            malayalam_pdf_key='pdfs/1/demo_ml.pdf',
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student,
            course=self.course,
            is_active=True,
        )
        self.session = DoubtSession.objects.create(
            student=self.student,
            instructor=self.instructor,
            course=self.course,
            status=DoubtSession.Status.CONFIRMED,
        )

    @patch('utils.r2_storage.delete_file')
    @patch('utils.pinecone_client.delete_video_chunks')
    def test_admin_can_delete_entire_course(self, delete_chunks_mock, delete_file_mock):
        self.client.login(username='admin_user', password='pass123')

        response = self.client.post(reverse('delete-course', args=[self.course.id]))

        self.assertRedirects(response, reverse('course-list'))
        self.assertFalse(Course.objects.filter(id=self.course.id).exists())
        self.assertFalse(Video.objects.filter(id=self.video.id).exists())
        self.assertFalse(Enrollment.objects.filter(id=self.enrollment.id).exists())

        self.session.refresh_from_db()
        self.assertIsNone(self.session.course)

        delete_chunks_mock.assert_called_once_with(self.video.id)
        self.assertEqual(delete_file_mock.call_count, 3)

    def test_non_admin_cannot_delete_course(self):
        self.client.login(username='instructor_user', password='pass123')

        response = self.client.post(reverse('delete-course', args=[self.course.id]))

        self.assertRedirects(
            response,
            reverse('instructor-dashboard'),
            fetch_redirect_response=False,
        )
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())


class StudentCourseBrowseTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='trainer_browse',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.student = User.objects.create_user(
            username='student_browse',
            password='pass123',
            role=User.Role.STUDENT,
        )
        self.enrolled_course = Course.objects.create(
            title='Enrolled Course',
            instructor=self.instructor,
            is_active=True,
        )
        self.available_course = Course.objects.create(
            title='Available Course',
            instructor=self.instructor,
            is_active=True,
        )
        Enrollment.objects.create(
            student=self.student,
            course=self.enrolled_course,
            is_active=True,
        )
        Video.objects.create(
            course=self.available_course,
            title='Locked Lesson',
            status=Video.Status.READY,
            english_transcript='Protected lesson notes',
        )

    def test_student_course_list_shows_all_active_courses(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse('course-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enrolled Course')
        self.assertContains(response, 'Available Course')
        self.assertContains(response, 'Available to enroll')

    def test_student_can_view_unenrolled_course_without_accessing_content(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse('course-detail', args=[self.available_course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Course content is locked')
        self.assertNotContains(response, 'Locked Lesson')
