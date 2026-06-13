from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from quizzes.models import Quiz, QuizDraft
from users.models import User
from videos.models import Video


class QuizGenerationFlowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin',
            password='pass123',
            role=User.Role.ADMIN,
        )
        self.instructor = User.objects.create_user(
            username='teacher',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.course = Course.objects.create(
            title='Physics',
            instructor=self.instructor,
            is_active=True,
        )
        self.video = Video.objects.create(
            course=self.course,
            title='Forces',
            english_transcript='Force equals mass times acceleration.',
            status=Video.Status.PROCESSING,
        )

    def test_generate_quiz_view_allows_transcript_before_ready(self):
        self.client.force_login(self.admin)

        with patch('videos.tasks.queue_quiz_generation', return_value=True) as mock_queue:
            response = self.client.post(reverse('generate-quiz', args=[self.video.id]))

        self.assertRedirects(response, reverse('video-detail', args=[self.video.id]))
        mock_queue.assert_called_once_with(self.video.id)

    def test_generate_quiz_view_requires_study_material(self):
        self.video.english_transcript = ''
        self.video.save(update_fields=['english_transcript'])
        self.client.force_login(self.admin)

        with patch('videos.tasks.queue_quiz_generation') as mock_queue:
            response = self.client.post(reverse('generate-quiz', args=[self.video.id]))

        self.assertRedirects(response, reverse('video-detail', args=[self.video.id]))
        mock_queue.assert_not_called()

    def test_queue_quiz_generation_skips_when_pending_draft_exists(self):
        QuizDraft.objects.create(
            video=self.video,
            question_text='Existing draft',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )

        from videos.tasks import queue_quiz_generation

        with patch('videos.tasks.generate_quiz.delay') as mock_delay:
            queued = queue_quiz_generation(self.video.id)

        self.assertFalse(queued)
        mock_delay.assert_not_called()


class StudyMaterialUploadStatusTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='teacher2',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.course = Course.objects.create(
            title='Chemistry',
            instructor=self.instructor,
            is_active=True,
        )
        self.video = Video.objects.create(
            course=self.course,
            title='Atoms',
            status=Video.Status.UPLOADED,
        )

    def test_uploading_study_material_marks_video_processing_immediately(self):
        self.client.force_login(self.instructor)

        with patch('videos.tasks.process_study_material.delay'):
            response = self.client.post(
                reverse('upload-material', args=[self.video.id]),
                {
                    'english_content': 'Atoms contain protons, neutrons, and electrons.',
                    'malayalam_content': '',
                },
            )

        self.assertRedirects(response, reverse('video-detail', args=[self.video.id]))
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PROCESSING)
        self.assertTrue(self.video.english_transcript)
        self.assertIsNotNone(self.video.processing_started_at)

    def test_video_detail_repairs_stale_uploaded_status_when_transcript_exists(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.status = Video.Status.UPLOADED
        self.video.save(update_fields=['english_transcript', 'status'])

        self.client.force_login(self.instructor)
        response = self.client.get(reverse('video-detail', args=[self.video.id]))

        self.assertEqual(response.status_code, 200)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PROCESSING)

    def test_video_detail_times_out_processing_after_three_minutes(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.status = Video.Status.PROCESSING
        self.video.processing_started_at = timezone.now() - Video.PROCESSING_TIMEOUT - timedelta(seconds=1)
        self.video.save(update_fields=['english_transcript', 'status', 'processing_started_at'])

        self.client.force_login(self.instructor)
        response = self.client.get(reverse('video-detail', args=[self.video.id]))

        self.assertEqual(response.status_code, 200)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.FAILED)
        self.assertIsNone(self.video.processing_started_at)

    def test_generate_quiz_redirects_to_draft_list_when_drafts_already_exist(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.save(update_fields=['english_transcript'])
        QuizDraft.objects.create(
            video=self.video,
            question_text='Existing reviewable draft',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )
        self.client.force_login(self.instructor)

        response = self.client.post(reverse('generate-quiz', args=[self.video.id]))

        self.assertRedirects(response, reverse('quiz-draft-list'))

    def test_generate_quiz_shows_published_quiz_message_only_when_published_quiz_exists(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.save(update_fields=['english_transcript'])
        Quiz.objects.create(
            video=self.video,
            title='Atoms Quiz',
            is_published=True,
        )
        self.client.force_login(self.instructor)

        response = self.client.post(reverse('generate-quiz', args=[self.video.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        messages = [m.message for m in response.context['messages']]
        self.assertIn(
            'Quiz generation was skipped because this content already has a published quiz.',
            messages,
        )
