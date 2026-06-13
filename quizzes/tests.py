from django.test import TestCase
from django.urls import reverse

from courses.models import Course, Enrollment
from quizzes.models import Quiz, QuizDraft
from users.models import User
from videos.models import Video


class QuizDraftReviewerAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.other_instructor = User.objects.create_user(
            username='other',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.admin = User.objects.create_user(
            username='admin',
            password='pass123',
            role=User.Role.ADMIN,
        )
        self.course = Course.objects.create(
            title='Biology',
            instructor=self.owner,
            is_active=True,
        )
        self.video = Video.objects.create(
            course=self.course,
            title='Cell Structure',
            english_transcript='Cells are the basic unit of life.',
            status=Video.Status.PROCESSING,
        )

    def test_instructor_can_view_own_pending_drafts(self):
        draft = QuizDraft.objects.create(
            video=self.video,
            question_text='What is the basic unit of life?',
            option_a='Cell',
            option_b='Atom',
            option_c='Tissue',
            option_d='Organ',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse('quiz-draft-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, draft.question_text)

    def test_instructor_cannot_review_other_instructors_draft(self):
        draft = QuizDraft.objects.create(
            video=self.video,
            question_text='Owner-only draft',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )

        self.client.force_login(self.other_instructor)
        response = self.client.get(reverse('review-quiz-draft', args=[draft.id]))

        self.assertRedirects(response, reverse('instructor-dashboard'))
        draft.refresh_from_db()
        self.assertEqual(draft.status, QuizDraft.Status.PENDING)

    def test_instructor_can_publish_approved_drafts_for_own_course(self):
        QuizDraft.objects.create(
            video=self.video,
            question_text='Approved question',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.APPROVED,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('publish-quiz', args=[self.video.id]),
            {'title': 'Biology Quiz 1'},
        )

        self.assertRedirects(response, reverse('quiz-draft-list'))
        quiz = Quiz.objects.get(video=self.video)
        self.assertTrue(quiz.is_published)
        self.assertEqual(quiz.questions.count(), 1)
        self.assertFalse(QuizDraft.objects.filter(video=self.video).exists())

    def test_instructor_draft_list_shows_approved_drafts_ready_to_publish(self):
        QuizDraft.objects.create(
            video=self.video,
            question_text='Approved question',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.APPROVED,
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse('quiz-draft-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ready to Publish')
        self.assertContains(response, self.video.title)

    def test_instructor_can_bulk_approve_all_pending_drafts(self):
        draft1 = QuizDraft.objects.create(
            video=self.video,
            question_text='First pending question',
            option_a='A1',
            option_b='B1',
            option_c='C1',
            option_d='D1',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )
        draft2 = QuizDraft.objects.create(
            video=self.video,
            question_text='Second pending question',
            option_a='A2',
            option_b='B2',
            option_c='C2',
            option_d='D2',
            correct_option='b',
            status=QuizDraft.Status.PENDING,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('bulk-review-quiz-drafts', args=[self.video.id]),
            {
                'action': 'approve_all',
                f'{draft1.id}_question_text': 'First updated question',
                f'{draft1.id}_option_a': 'A1',
                f'{draft1.id}_option_b': 'B1',
                f'{draft1.id}_option_c': 'C1',
                f'{draft1.id}_option_d': 'D1',
                f'{draft1.id}_correct_option': 'c',
                f'{draft1.id}_explanation': 'Updated explanation 1',
                f'{draft2.id}_question_text': 'Second updated question',
                f'{draft2.id}_option_a': 'A2',
                f'{draft2.id}_option_b': 'B2',
                f'{draft2.id}_option_c': 'C2',
                f'{draft2.id}_option_d': 'D2',
                f'{draft2.id}_correct_option': 'd',
                f'{draft2.id}_explanation': 'Updated explanation 2',
            },
        )

        self.assertRedirects(response, reverse('quiz-draft-list'))
        draft1.refresh_from_db()
        draft2.refresh_from_db()
        self.assertEqual(draft1.status, QuizDraft.Status.APPROVED)
        self.assertEqual(draft2.status, QuizDraft.Status.APPROVED)
        self.assertEqual(draft1.question_text, 'First updated question')
        self.assertEqual(draft2.correct_option, 'd')

    def test_bulk_approve_and_publish_makes_quiz_visible_to_enrolled_student(self):
        student = User.objects.create_user(
            username='student1',
            password='pass123',
            role=User.Role.STUDENT,
        )
        Enrollment.objects.create(student=student, course=self.course, is_active=True)
        draft = QuizDraft.objects.create(
            video=self.video,
            question_text='Pending question',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('bulk-review-quiz-drafts', args=[self.video.id]),
            {
                'action': 'approve_and_publish',
                'title': 'Published Biology Quiz',
                f'{draft.id}_question_text': 'Published question',
                f'{draft.id}_option_a': 'A',
                f'{draft.id}_option_b': 'B',
                f'{draft.id}_option_c': 'C',
                f'{draft.id}_option_d': 'D',
                f'{draft.id}_correct_option': 'b',
                f'{draft.id}_explanation': 'Explanation',
            },
        )

        self.assertRedirects(response, reverse('quiz-draft-list'))
        quiz = Quiz.objects.get(video=self.video)
        self.assertTrue(quiz.is_published)
        self.assertEqual(quiz.questions.count(), 1)
        self.assertFalse(QuizDraft.objects.filter(video=self.video).exists())

        self.client.force_login(student)
        response = self.client.get(reverse('student-quiz-list'))
        self.assertContains(response, 'Published Biology Quiz')

    def test_student_quiz_list_shows_learning_assistant_for_enrolled_student(self):
        student = User.objects.create_user(
            username='student2',
            password='pass123',
            role=User.Role.STUDENT,
        )
        Enrollment.objects.create(student=student, course=self.course, is_active=True)

        self.client.force_login(student)
        response = self.client.get(reverse('student-quiz-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Learning Assistant')
