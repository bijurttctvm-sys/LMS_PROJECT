from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course, Enrollment

from .models import DoubtSession, InstructorSlot


User = get_user_model()


class StudentSessionsPageTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='student_user',
            password='pass123',
            role=User.Role.STUDENT,
            email='student@example.com',
        )
        self.instructor = User.objects.create_user(
            username='instructor_user',
            password='pass123',
            role=User.Role.INSTRUCTOR,
            email='instructor@example.com',
            google_meet_link='https://meet.google.com/abc-defg-rft',
        )
        self.course = Course.objects.create(
            title='Python',
            instructor=self.instructor,
        )
        Enrollment.objects.create(
            student=self.student,
            course=self.course,
            is_active=True,
        )

    def test_my_sessions_shows_instructor_email_and_meet_link(self):
        slot = InstructorSlot.objects.create(
            instructor=self.instructor,
            slot_datetime=timezone.now() + timedelta(days=1),
            is_available=False,
        )
        DoubtSession.objects.create(
            student=self.student,
            instructor=self.instructor,
            course=self.course,
            slot=slot,
            status=DoubtSession.Status.CONFIRMED,
            meet_url='https://meet.google.com/abc-defg-rft',
            request_message='Need help with decorators',
        )

        self.client.login(username='student_user', password='pass123')
        response = self.client.get(reverse('my-sessions'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'instructor@example.com')
        self.assertContains(response, 'https://meet.google.com/abc-defg-rft')


class PostponedSessionWorkflowTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='student_postpone',
            password='pass123',
            role=User.Role.STUDENT,
            email='student2@example.com',
        )
        self.instructor = User.objects.create_user(
            username='instructor_postpone',
            password='pass123',
            role=User.Role.INSTRUCTOR,
            email='instructor2@example.com',
            google_meet_link='https://meet.google.com/abc-defg-rft',
        )
        self.course = Course.objects.create(
            title='Python Advanced',
            instructor=self.instructor,
        )
        Enrollment.objects.create(
            student=self.student,
            course=self.course,
            is_active=True,
        )

    def test_instructor_can_postpone_once_and_repropose_slots(self):
        slot = InstructorSlot.objects.create(
            instructor=self.instructor,
            slot_datetime=timezone.now() + timedelta(days=1),
            is_available=False,
        )
        session = DoubtSession.objects.create(
            student=self.student,
            instructor=self.instructor,
            course=self.course,
            slot=slot,
            status=DoubtSession.Status.CONFIRMED,
            meet_url='https://meet.google.com/abc-defg-rft',
        )

        self.client.login(username='instructor_postpone', password='pass123')
        postpone_response = self.client.post(
            reverse('mark-outcome', args=[session.id]),
            {'outcome': 'postponed'},
        )

        self.assertRedirects(postpone_response, reverse('instructor-sessions'))
        session.refresh_from_db()
        slot.refresh_from_db()
        self.assertEqual(session.status, DoubtSession.Status.POSTPONED)
        self.assertTrue(session.instructor_postponed_once)
        self.assertIsNone(session.slot)
        self.assertEqual(session.meet_url, '')
        self.assertTrue(slot.is_available)

        repropose_response = self.client.post(
            reverse('propose-slots', args=[session.id]),
            {
                'slot_1': timezone.localtime(timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'slot_2': timezone.localtime(timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
                'slot_3': timezone.localtime(timezone.now() + timedelta(days=4)).strftime('%Y-%m-%dT%H:%M'),
            },
        )

        self.assertRedirects(repropose_response, reverse('instructor-sessions'))
        session.refresh_from_db()
        self.assertEqual(session.status, DoubtSession.Status.SELECTED)
        self.assertTrue(session.instructor_postponed_once)
        self.assertEqual(session.proposed_slots.count(), 3)

    def test_instructor_cannot_postpone_more_than_once(self):
        slot = InstructorSlot.objects.create(
            instructor=self.instructor,
            slot_datetime=timezone.now() + timedelta(days=1),
            is_available=False,
        )
        session = DoubtSession.objects.create(
            student=self.student,
            instructor=self.instructor,
            course=self.course,
            slot=slot,
            status=DoubtSession.Status.CONFIRMED,
            instructor_postponed_once=True,
        )

        self.client.login(username='instructor_postpone', password='pass123')
        response = self.client.post(
            reverse('mark-outcome', args=[session.id]),
            {'outcome': 'postponed'},
        )

        self.assertRedirects(response, reverse('instructor-sessions'))
        session.refresh_from_db()
        self.assertEqual(session.status, DoubtSession.Status.CONFIRMED)

    def test_my_sessions_falls_back_to_instructor_profile_meet_link(self):
        DoubtSession.objects.create(
            student=self.student,
            instructor=self.instructor,
            course=self.course,
            status=DoubtSession.Status.REQUESTED,
            request_message='Need help with lists',
        )

        self.client.login(username='student_postpone', password='pass123')
        response = self.client.get(reverse('my-sessions'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'instructor2@example.com')
        self.assertContains(response, 'https://meet.google.com/abc-defg-rft')
