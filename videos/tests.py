import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from quizzes.constants import QUIZ_TARGET_QUESTION_COUNT
from quizzes.models import Quiz, QuizDraft
from users.models import User
from .forms import StudyMaterialUploadForm
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

    def test_queue_quiz_generation_returns_none_when_delay_fails(self):
        from videos.tasks import queue_quiz_generation

        with patch('videos.tasks.generate_quiz.delay', side_effect=RuntimeError('broker down')):
            queued = queue_quiz_generation(self.video.id)

        self.assertIsNone(queued)

    def test_generate_quiz_view_shows_service_warning_when_queue_fails(self):
        self.client.force_login(self.admin)

        with patch('videos.tasks.queue_quiz_generation', return_value=None):
            response = self.client.post(
                reverse('generate-quiz', args=[self.video.id]),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        messages = [m.message for m in response.context['messages']]
        self.assertIn(
            'Quiz generation is temporarily unavailable. Please try again shortly.',
            messages,
        )
        self.assertNotIn(
            'Quiz generation was skipped because this content already has a quiz.',
            messages,
        )

    def test_generate_quiz_recovers_from_truncated_json_response(self):
        from videos.tasks import generate_quiz

        invalid_payload = (
            '[{"question":"Q1","option_a":"A","option_b":"B","option_c":"C",'
            '"option_d":"D","correct_option":"a","explanation":"Starts valid"},'
            '{"question":"Q2","option_a":"A","option_b":"B","option_c":"C",'
            '"option_d":"D","correct_option":"b","explanation":"Truncated'
        )
        valid_payload = json.dumps([
            {
                'question': f'Question {i + 1}',
                'option_a': 'Option A',
                'option_b': 'Option B',
                'option_c': 'Option C',
                'option_d': 'Option D',
                'correct_option': 'a',
                'explanation': 'Because it matches the study material.',
            }
            for i in range(QUIZ_TARGET_QUESTION_COUNT)
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            MagicMock(choices=[MagicMock(message=MagicMock(content=invalid_payload))]),
            MagicMock(choices=[MagicMock(message=MagicMock(content=valid_payload))]),
        ]

        with patch('groq.Groq', return_value=mock_client):
            result = generate_quiz.apply(args=[self.video.id])

        self.assertTrue(result.successful())
        self.assertEqual(
            QuizDraft.objects.filter(video=self.video, status=QuizDraft.Status.PENDING).count(),
            QUIZ_TARGET_QUESTION_COUNT,
        )
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    def test_generate_replacement_quiz_draft_retries_when_first_candidate_is_duplicate(self):
        existing = QuizDraft.objects.create(
            video=self.video,
            question_text='What is force?',
            option_a='Push or pull',
            option_b='Energy',
            option_c='Mass',
            option_d='Speed',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )

        duplicate_payload = json.dumps([{
            'question': 'What is force?',
            'option_a': 'Push or pull',
            'option_b': 'Energy',
            'option_c': 'Mass',
            'option_d': 'Speed',
            'correct_option': 'a',
            'explanation': 'A force is a push or pull.',
        }])
        unique_payload = json.dumps([{
            'question': 'Why can force change the motion of an object?',
            'option_a': 'Because it changes velocity',
            'option_b': 'Because it changes color',
            'option_c': 'Because it removes mass',
            'option_d': 'Because it stops gravity',
            'correct_option': 'a',
            'explanation': 'Force can accelerate an object by changing its velocity.',
        }])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            MagicMock(choices=[MagicMock(message=MagicMock(content=duplicate_payload))]),
            MagicMock(choices=[MagicMock(message=MagicMock(content=unique_payload))]),
        ]

        from videos.tasks import generate_replacement_quiz_draft

        with patch('groq.Groq', return_value=mock_client):
            replacement = generate_replacement_quiz_draft(
                self.video.id,
                rejected_question=existing.question_text,
                rejection_note='Duplicate of another draft.',
            )

        self.assertEqual(replacement.status, QuizDraft.Status.PENDING)
        self.assertEqual(
            replacement.question_text,
            'Why can force change the motion of an object?',
        )
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    def test_generate_replacement_quiz_drafts_matches_rejected_count(self):
        first = QuizDraft.objects.create(
            video=self.video,
            question_text='Replacement 1',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )
        second = QuizDraft.objects.create(
            video=self.video,
            question_text='Replacement 2',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )

        from videos.tasks import generate_replacement_quiz_drafts

        with patch(
            'videos.tasks.generate_replacement_quiz_draft',
            side_effect=[first, second],
        ) as mock_generate:
            replacements = generate_replacement_quiz_drafts(
                self.video.id,
                rejected_questions=['Rejected 1', 'Rejected 2'],
                rejection_note='Needs variety.',
            )

        self.assertEqual(replacements, [first, second])
        self.assertEqual(mock_generate.call_count, 2)


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

    def test_video_detail_shows_generate_quiz_questions_workflow_action_when_no_drafts_exist(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.save(update_fields=['english_transcript'])
        self.client.force_login(self.instructor)

        response = self.client.get(reverse('video-detail', args=[self.video.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Generate Quiz Questions')
        self.assertContains(response, reverse('continue-quiz-workflow', args=[self.video.id]))

    def test_video_detail_shows_approve_pending_quiz_questions_action(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.save(update_fields=['english_transcript'])
        QuizDraft.objects.create(
            video=self.video,
            question_text='Pending video detail draft',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.PENDING,
        )
        self.client.force_login(self.instructor)

        response = self.client.get(reverse('video-detail', args=[self.video.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approve Pending Quiz Questions')
        self.assertContains(response, reverse('continue-quiz-workflow', args=[self.video.id]))

    def test_video_detail_shows_generate_more_questions_action_when_approved_set_is_incomplete(self):
        self.video.english_transcript = 'Atomic structure notes'
        self.video.save(update_fields=['english_transcript'])
        QuizDraft.objects.create(
            video=self.video,
            question_text='Approved detail draft',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='a',
            status=QuizDraft.Status.APPROVED,
        )
        self.client.force_login(self.instructor)

        response = self.client.get(reverse('video-detail', args=[self.video.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'Generate {QUIZ_TARGET_QUESTION_COUNT - 1} More Quiz Questions',
        )
        self.assertContains(response, reverse('continue-quiz-workflow', args=[self.video.id]))

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


class VideoAccessControlTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner_trainer',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.other_instructor = User.objects.create_user(
            username='other_trainer',
            password='pass123',
            role=User.Role.INSTRUCTOR,
        )
        self.course = Course.objects.create(
            title='Protected Course',
            instructor=self.owner,
            is_active=True,
        )
        self.video = Video.objects.create(
            course=self.course,
            title='Protected Video',
            english_transcript='Protected notes',
            status=Video.Status.READY,
        )

    def test_other_instructor_cannot_open_another_instructors_video(self):
        self.client.force_login(self.other_instructor)

        response = self.client.get(reverse('video-detail', args=[self.video.id]))

        self.assertRedirects(response, reverse('course-list'))

    def test_other_instructor_cannot_manage_another_instructors_video(self):
        self.client.force_login(self.other_instructor)

        response = self.client.post(reverse('generate-quiz', args=[self.video.id]))

        self.assertRedirects(response, reverse('course-list'))


class StudyMaterialUploadFormSecurityTests(TestCase):
    def test_rejects_disallowed_file_extensions(self):
        form = StudyMaterialUploadForm(
            data={'english_content': '', 'malayalam_content': ''},
            files={
                'material_file': SimpleUploadedFile(
                    'payload.exe',
                    b'bad-data',
                    content_type='application/octet-stream',
                )
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('material_file', form.errors)
