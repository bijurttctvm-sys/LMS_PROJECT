from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from doubt_sessions.models import DoubtSession
from videos.models import Video

from .models import Batch, BatchCourse, BatchStudent, Course, Enrollment, EnrollmentRequest


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
        self.assertContains(response, 'Request Access')

    def test_student_can_view_unenrolled_course_without_accessing_content(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse('course-detail', args=[self.available_course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Course content is locked')
        self.assertNotContains(response, 'Locked Lesson')


class CourseAccessRequestTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='access_admin',
            password='pass123',
            role=User.Role.ADMIN,
        )
        self.instructor = User.objects.create_user(
            username='access_trainer',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.student = User.objects.create_user(
            username='access_student',
            password='pass123',
            role=User.Role.STUDENT,
            email='access_student@example.com',
        )
        self.course = Course.objects.create(
            title='Requested Course',
            instructor=self.instructor,
            is_active=True,
        )
        Video.objects.create(
            course=self.course,
            title='Protected Lesson',
            status=Video.Status.READY,
            english_transcript='Protected lesson notes',
        )

    def test_student_can_submit_course_access_request(self):
        self.client.force_login(self.student)

        response = self.client.post(
            reverse('request-course-access', args=[self.course.id]),
            {'request_reason': 'I need this course to complete my Python training plan.'},
        )

        self.assertRedirects(response, reverse('course-detail', args=[self.course.id]))
        access_request = EnrollmentRequest.objects.get(student=self.student, course=self.course)
        self.assertEqual(access_request.status, EnrollmentRequest.Status.PENDING)
        self.assertEqual(
            access_request.request_reason,
            'I need this course to complete my Python training plan.',
        )

        follow_up = self.client.get(reverse('course-detail', args=[self.course.id]))
        self.assertContains(follow_up, 'pending admin approval')
        self.assertContains(follow_up, 'Request Pending')
        self.assertContains(follow_up, 'I need this course to complete my Python training plan.')

    def test_student_must_provide_reason_for_course_access_request(self):
        self.client.force_login(self.student)

        response = self.client.post(
            reverse('request-course-access', args=[self.course.id]),
            {'request_reason': '   '},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please provide a reason for requesting access.')
        self.assertFalse(
            EnrollmentRequest.objects.filter(student=self.student, course=self.course).exists()
        )

    def test_student_course_list_shows_pending_request_state(self):
        EnrollmentRequest.objects.create(
            student=self.student,
            course=self.course,
            status=EnrollmentRequest.Status.PENDING,
            request_reason='Need access for next module.',
        )
        self.client.force_login(self.student)

        response = self.client.get(reverse('course-list'))

        self.assertContains(response, 'Request Pending')

    def test_admin_can_approve_course_access_request_and_create_enrollment(self):
        access_request = EnrollmentRequest.objects.create(
            student=self.student,
            course=self.course,
            status=EnrollmentRequest.Status.PENDING,
            request_reason='Need access for project preparation.',
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('review-enrollment-request', args=[access_request.id]),
            {
                'action': 'approve',
                'status': EnrollmentRequest.Status.PENDING,
                'admin_note': 'Approved because the trainee is part of the current intake.',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('manage-enrollment-requests')}?status=pending",
            fetch_redirect_response=False,
        )
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, EnrollmentRequest.Status.APPROVED)
        self.assertEqual(
            access_request.admin_note,
            'Approved because the trainee is part of the current intake.',
        )
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.course, is_active=True).exists()
        )

    def test_admin_must_add_reason_before_approving_access_request(self):
        access_request = EnrollmentRequest.objects.create(
            student=self.student,
            course=self.course,
            status=EnrollmentRequest.Status.PENDING,
            request_reason='Need access for project preparation.',
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('review-enrollment-request', args=[access_request.id]),
            {
                'action': 'approve',
                'status': EnrollmentRequest.Status.PENDING,
                'admin_note': '   ',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('manage-enrollment-requests')}?status=pending",
            fetch_redirect_response=False,
        )
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, EnrollmentRequest.Status.PENDING)
        self.assertFalse(
            Enrollment.objects.filter(student=self.student, course=self.course, is_active=True).exists()
        )

    def test_admin_can_reject_course_access_request(self):
        access_request = EnrollmentRequest.objects.create(
            student=self.student,
            course=self.course,
            status=EnrollmentRequest.Status.PENDING,
            request_reason='Need access but missing prerequisites right now.',
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('review-enrollment-request', args=[access_request.id]),
            {
                'action': 'reject',
                'status': EnrollmentRequest.Status.PENDING,
                'admin_note': 'Rejected because the trainee must finish the beginner course first.',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('manage-enrollment-requests')}?status=pending",
            fetch_redirect_response=False,
        )
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, EnrollmentRequest.Status.REJECTED)
        self.assertEqual(
            access_request.admin_note,
            'Rejected because the trainee must finish the beginner course first.',
        )
        self.assertFalse(
            Enrollment.objects.filter(student=self.student, course=self.course, is_active=True).exists()
        )

        self.client.force_login(self.student)
        follow_up = self.client.get(reverse('course-detail', args=[self.course.id]))
        self.assertContains(follow_up, 'Rejected because the trainee must finish the beginner course first.')

    def test_admin_request_queue_lists_pending_requests(self):
        EnrollmentRequest.objects.create(
            student=self.student,
            course=self.course,
            status=EnrollmentRequest.Status.PENDING,
            request_reason='Please approve this so I can join the upcoming batch.',
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('manage-enrollment-requests'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Course Access Requests')
        self.assertContains(response, self.student.username)
        self.assertContains(response, self.course.title)
        self.assertContains(response, 'Please approve this so I can join the upcoming batch.')
        self.assertContains(response, 'Reason for your decision')


class AdminCourseManagementViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='course_admin',
            password='pass123',
            role=User.Role.ADMIN,
        )
        self.instructor = User.objects.create_user(
            username='course_trainer',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.course = Course.objects.create(
            title='Admin Managed Course',
            instructor=self.instructor,
            is_active=True,
        )
        self.video = Video.objects.create(
            course=self.course,
            title='Admin Lesson',
            status=Video.Status.READY,
            english_transcript='Admin notes',
        )
        self.student = User.objects.create_user(
            username='course_student',
            password='pass123',
            role=User.Role.STUDENT,
        )
        Enrollment.objects.create(
            student=self.student,
            course=self.course,
            is_active=True,
        )

    def test_admin_modify_course_page_shows_management_actions(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('edit-course', args=[self.course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reassign Trainer')
        self.assertContains(response, reverse('assign-instructor', args=[self.course.id]))
        self.assertContains(response, 'Delete Content')
        self.assertContains(response, reverse('delete-course-content', args=[self.course.id]))
        self.assertContains(response, 'Delete Course')
        self.assertContains(response, reverse('delete-course', args=[self.course.id]))

    def test_admin_course_detail_shows_management_actions(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('course-detail', args=[self.course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Modify Course')
        self.assertContains(response, 'Reassign Trainer')
        self.assertContains(response, 'Delete Content')
        self.assertContains(response, 'Delete Course')


class BatchManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='batch_admin',
            password='pass123',
            role=User.Role.ADMIN,
        )
        self.instructor = User.objects.create_user(
            username='batch_trainer',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.student = User.objects.create_user(
            username='batch_student',
            password='pass123',
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            title='Batch Course',
            instructor=self.instructor,
            is_active=True,
        )
        self.batch = Batch.objects.create(name='Morning Batch')

    def test_assigning_course_to_batch_enrolls_existing_batch_students(self):
        BatchStudent.objects.create(batch=self.batch, student=self.student, is_active=True)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('assign-courses-to-batch', args=[self.batch.id]),
            {'course_ids': [str(self.course.id)]},
        )

        self.assertRedirects(
            response,
            f"{reverse('batch-list')}?manage=assign-courses",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            BatchCourse.objects.filter(batch=self.batch, course=self.course, is_active=True).exists()
        )
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.course, is_active=True).exists()
        )

    def test_adding_student_to_batch_enrolls_them_in_existing_batch_courses(self):
        BatchCourse.objects.create(batch=self.batch, course=self.course, is_active=True)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('assign-students-to-batch', args=[self.batch.id]),
            {'student_ids': [str(self.student.id)]},
        )

        self.assertRedirects(
            response,
            f"{reverse('batch-list')}?manage=assign-students",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            BatchStudent.objects.filter(batch=self.batch, student=self.student, is_active=True).exists()
        )
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.course, is_active=True).exists()
        )
