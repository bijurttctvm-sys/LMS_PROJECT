from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from courses.models import Course, Enrollment
from quizzes.constants import QUIZ_TARGET_QUESTION_COUNT
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

    def _create_draft(self, index, status=QuizDraft.Status.PENDING, question_text=None):
        return QuizDraft.objects.create(
            video=self.video,
            question_text=question_text or f'Question {index}',
            option_a=f'Option A{index}',
            option_b=f'Option B{index}',
            option_c=f'Option C{index}',
            option_d=f'Option D{index}',
            correct_option='a',
            explanation=f'Explanation {index}',
            status=status,
        )

    def _create_drafts(self, count, status=QuizDraft.Status.PENDING, prefix='Question'):
        return [
            self._create_draft(
                index=i + 1,
                status=status,
                question_text=f'{prefix} {i + 1}',
            )
            for i in range(count)
        ]

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

    def test_instructor_dashboard_shows_approve_pending_quiz_questions_action(self):
        self._create_draft(1, status=QuizDraft.Status.PENDING, question_text='Pending dashboard draft')

        self.client.force_login(self.owner)
        response = self.client.get(reverse('instructor-dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approve Pending Quiz Questions')
        self.assertContains(response, reverse('review-pending-quiz-drafts'))

    def test_review_pending_quiz_drafts_redirects_to_first_pending_video(self):
        self._create_draft(1, status=QuizDraft.Status.PENDING, question_text='Pending approval question')

        self.client.force_login(self.owner)
        response = self.client.get(reverse('review-pending-quiz-drafts'))

        self.assertRedirects(response, reverse('bulk-review-quiz-drafts', args=[self.video.id]))

    def test_continue_quiz_workflow_redirects_to_bulk_review_when_pending_exists(self):
        self._create_draft(1, status=QuizDraft.Status.PENDING, question_text='Pending workflow question')

        self.client.force_login(self.owner)
        response = self.client.post(reverse('continue-quiz-workflow', args=[self.video.id]))

        self.assertRedirects(response, reverse('bulk-review-quiz-drafts', args=[self.video.id]))

    def test_continue_quiz_workflow_queues_initial_generation_when_no_drafts_exist(self):
        self.client.force_login(self.owner)

        with patch('videos.tasks.queue_quiz_generation', return_value=True) as mock_queue:
            response = self.client.post(reverse('continue-quiz-workflow', args=[self.video.id]))

        self.assertRedirects(response, reverse('video-detail', args=[self.video.id]))
        mock_queue.assert_called_once_with(self.video.id)

    def test_continue_quiz_workflow_generates_missing_questions_when_approved_set_is_incomplete(self):
        self._create_drafts(
            QUIZ_TARGET_QUESTION_COUNT - 2,
            status=QuizDraft.Status.APPROVED,
            prefix='Approved workflow question',
        )

        def create_missing(video_id, missing_count, note=''):
            return [
                self._create_draft(
                    index=200 + i,
                    status=QuizDraft.Status.PENDING,
                    question_text=f'Generated pending workflow question {i + 1}',
                )
                for i in range(missing_count)
            ]

        self.client.force_login(self.owner)
        with patch('videos.tasks.generate_missing_quiz_drafts', side_effect=create_missing) as mock_generate:
            response = self.client.post(reverse('continue-quiz-workflow', args=[self.video.id]))

        self.assertRedirects(response, reverse('bulk-review-quiz-drafts', args=[self.video.id]))
        mock_generate.assert_called_once_with(
            self.video.id,
            2,
            note='Generate the remaining unique quiz questions needed to complete this quiz set.',
        )

    def test_continue_quiz_workflow_redirects_to_publish_when_all_questions_are_approved(self):
        self._create_drafts(
            QUIZ_TARGET_QUESTION_COUNT,
            status=QuizDraft.Status.APPROVED,
            prefix='Approved publish question',
        )

        self.client.force_login(self.owner)
        response = self.client.post(reverse('continue-quiz-workflow', args=[self.video.id]))

        self.assertRedirects(response, reverse('publish-quiz', args=[self.video.id]))

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
        self._create_drafts(QUIZ_TARGET_QUESTION_COUNT, status=QuizDraft.Status.APPROVED, prefix='Approved question')

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('publish-quiz', args=[self.video.id]),
            {'title': 'Biology Quiz 1'},
        )

        self.assertRedirects(response, reverse('quiz-draft-list'))
        quiz = Quiz.objects.get(video=self.video)
        self.assertTrue(quiz.is_published)
        self.assertEqual(quiz.questions.count(), QUIZ_TARGET_QUESTION_COUNT)
        self.assertFalse(QuizDraft.objects.filter(video=self.video).exists())

    def test_instructor_draft_list_shows_approval_progress_until_ten_questions_are_ready(self):
        self._create_draft(1, status=QuizDraft.Status.APPROVED, question_text='Approved question')

        self.client.force_login(self.owner)
        response = self.client.get(reverse('quiz-draft-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approval Progress')
        self.assertContains(response, f'1 / {QUIZ_TARGET_QUESTION_COUNT} approved')
        self.assertContains(response, self.video.title)
        self.assertNotContains(response, 'Publish Quiz')

    def test_draft_list_does_not_repeat_video_in_approved_section_when_pending_exists(self):
        self._create_draft(1, status=QuizDraft.Status.PENDING, question_text='Pending question')
        self._create_draft(2, status=QuizDraft.Status.APPROVED, question_text='Approved question')

        self.client.force_login(self.owner)
        response = self.client.get(reverse('quiz-draft-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending Review')
        self.assertNotContains(response, 'Approval Progress')
        self.assertContains(response, 'Approve Pending Quiz Questions')

    def test_publish_is_blocked_when_fewer_than_ten_questions_are_approved(self):
        self._create_drafts(
            QUIZ_TARGET_QUESTION_COUNT - 1,
            status=QuizDraft.Status.APPROVED,
            prefix='Almost ready',
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('publish-quiz', args=[self.video.id]),
            {'title': 'Biology Quiz 1'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Quiz.objects.filter(video=self.video).exists())
        messages = [m.message for m in response.context['messages']]
        self.assertIn(
            f'Publishing is available only after exactly {QUIZ_TARGET_QUESTION_COUNT} questions are approved. Current progress: {QUIZ_TARGET_QUESTION_COUNT - 1}/{QUIZ_TARGET_QUESTION_COUNT} approved and 0 pending.',
            messages,
        )

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
        drafts = self._create_drafts(
            QUIZ_TARGET_QUESTION_COUNT,
            status=QuizDraft.Status.PENDING,
            prefix='Pending question',
        )
        post_data = {
            'action': 'approve_and_publish',
            'title': 'Published Biology Quiz',
        }
        for index, draft in enumerate(drafts, start=1):
            post_data.update({
                f'{draft.id}_question_text': f'Published question {index}',
                f'{draft.id}_option_a': f'A{index}',
                f'{draft.id}_option_b': f'B{index}',
                f'{draft.id}_option_c': f'C{index}',
                f'{draft.id}_option_d': f'D{index}',
                f'{draft.id}_correct_option': 'b',
                f'{draft.id}_explanation': f'Explanation {index}',
            })

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('bulk-review-quiz-drafts', args=[self.video.id]),
            post_data,
        )

        self.assertRedirects(response, reverse('quiz-draft-list'))
        quiz = Quiz.objects.get(video=self.video)
        self.assertTrue(quiz.is_published)
        self.assertEqual(quiz.questions.count(), QUIZ_TARGET_QUESTION_COUNT)
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

    def test_rejecting_a_draft_generates_replacement_for_review(self):
        original = QuizDraft.objects.create(
            video=self.video,
            question_text='Original pending question',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )
        replacement = QuizDraft.objects.create(
            video=self.video,
            question_text='Replacement question',
            option_a='A2',
            option_b='B2',
            option_c='C2',
            option_d='D2',
            correct_option='b',
            status=QuizDraft.Status.PENDING,
        )

        self.client.force_login(self.owner)
        with patch('videos.tasks.generate_replacement_quiz_draft', return_value=replacement) as mock_generate:
            response = self.client.post(
                reverse('review-quiz-draft', args=[original.id]),
                {'action': 'reject', 'admin_note': 'Too similar to another question.'},
            )

        self.assertRedirects(response, reverse('review-quiz-draft', args=[replacement.id]))
        original.refresh_from_db()
        self.assertEqual(original.status, QuizDraft.Status.REJECTED)
        self.assertEqual(original.admin_note, 'Too similar to another question.')
        mock_generate.assert_called_once_with(
            self.video.id,
            rejected_question='Original pending question',
            rejection_note='Too similar to another question.',
        )

    def test_bulk_reject_all_generates_replacements_for_each_rejected_question(self):
        drafts = self._create_drafts(3, status=QuizDraft.Status.PENDING, prefix='Bulk question')

        def create_replacement(video_id, rejected_question='', rejection_note=''):
            index = QuizDraft.objects.filter(
                video=self.video,
                status=QuizDraft.Status.PENDING,
            ).count() + 1
            return QuizDraft.objects.create(
                video=self.video,
                question_text=f'Replacement {index} for {rejected_question}',
                option_a='A',
                option_b='B',
                option_c='C',
                option_d='D',
                correct_option='a',
                explanation=rejection_note,
                status=QuizDraft.Status.PENDING,
            )

        self.client.force_login(self.owner)
        with patch('videos.tasks.generate_replacement_quiz_draft', side_effect=create_replacement) as mock_generate:
            response = self.client.post(
                reverse('bulk-review-quiz-drafts', args=[self.video.id]),
                {'action': 'reject_all', 'admin_note': 'Needs better coverage.'},
            )

        self.assertRedirects(response, reverse('bulk-review-quiz-drafts', args=[self.video.id]))
        self.assertEqual(
            QuizDraft.objects.filter(video=self.video, status=QuizDraft.Status.REJECTED).count(),
            len(drafts),
        )
        self.assertEqual(
            QuizDraft.objects.filter(video=self.video, status=QuizDraft.Status.PENDING).count(),
            len(drafts),
        )
        self.assertEqual(mock_generate.call_count, len(drafts))
