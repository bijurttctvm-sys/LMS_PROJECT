import json
import re
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings

from courses.models import Course, Enrollment
from utils.groq_llm import _clean_answer_style

User = get_user_model()


class ChatbotQueryViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testchatbot", email="tc@test.com",
            password="pass123", role="student"
        )
        self.instructor = User.objects.create_user(
            username="teacherchatbot", email="teacher@test.com",
            password="pass123", role="instructor"
        )
        self.course = Course.objects.create(
            title="Python Basics",
            instructor=self.instructor,
            is_active=True,
        )
        Enrollment.objects.create(
            student=self.user,
            course=self.course,
            is_active=True,
        )
        self.client.login(username="testchatbot", password="pass123")

    def test_get_returns_405(self):
        r = self.client.get("/chatbot/query/")
        self.assertEqual(r.status_code, 405)

    def test_missing_query_returns_400(self):
        r = self.client.post("/chatbot/query/",
                             data=json.dumps({"course_id": self.course.id}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", json.loads(r.content))

    def test_missing_course_id_returns_400(self):
        r = self.client.post("/chatbot/query/",
                             data=json.dumps({"query": "hello"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_invalid_json_returns_400(self):
        r = self.client.post("/chatbot/query/", data="not-json",
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_pinecone_error_returns_503_json(self):
        """View must return JSON 503, not propagate infra exceptions as 500."""
        with patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024), \
             patch("utils.pinecone_client.search_chunks",
                   side_effect=Exception("Pinecone [401] Invalid API Key")), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({"query": "what is python", "course_id": self.course.id}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 503)
        body = json.loads(r.content)
        self.assertIn("error", body)
        self.assertIn("Vector search", body["error"])

    def test_groq_error_returns_503_json(self):
        """Groq failure also returns JSON 503."""
        with patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024), \
             patch("utils.pinecone_client.search_chunks", return_value=[]), \
             patch("utils.groq_llm.get_answer",
                   side_effect=Exception("Groq 401 Invalid API Key")), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({"query": "hello", "course_id": self.course.id}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 503)
        body = json.loads(r.content)
        self.assertIn("error", body)

    def test_successful_query_returns_200(self):
        """Happy path with mocked infra returns answer as JSON 200."""
        fake_chunks = [
            {"text": "Python is a language", "video_id": 1,
             "start": 0.0, "end": 5.0, "score": 0.9}
        ]
        with patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024), \
             patch("utils.pinecone_client.search_chunks",
                   return_value=fake_chunks), \
             patch("utils.groq_llm.get_answer",
                   return_value="Python is a general-purpose language."), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({"query": "what is python", "course_id": self.course.id}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertIn("answer", body)
        self.assertIn("language", body)
        self.assertIn("source_chunks", body)
        self.assertEqual(body["language"], "en")

    def test_selected_ml_language_returns_malayalam_answer_and_voice(self):
        fake_chunks = [
            {"text": "Python is a language", "video_id": 1,
             "start": 0.0, "end": 5.0, "score": 0.9}
        ]
        with patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024), \
             patch("utils.pinecone_client.search_chunks",
                   return_value=fake_chunks), \
             patch("utils.groq_llm.get_answer",
                   return_value="Python is a general-purpose language."), \
             patch("chatbot.views._sarvam_translate",
                   return_value="പൈത്തൺ ഒരു പൊതുവായ പ്രോഗ്രാമിംഗ് ഭാഷയാണ്."), \
             patch("chatbot.views._sarvam_tts",
                   return_value="FAKE_AUDIO"), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({
                    "query": "what is python",
                    "course_id": self.course.id,
                    "language": "ml",
                    "voice": True,
                }),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertEqual(body["language"], "ml")
        self.assertEqual(body["answer"], "പൈത്തൺ ഒരു പൊതുവായ പ്രോഗ്രാമിംഗ് ഭാഷയാണ്.")
        self.assertEqual(body["audio"], "FAKE_AUDIO")

    def test_deferred_voice_returns_text_first_and_skips_inline_tts(self):
        fake_chunks = [
            {"text": "Python is a language", "video_id": 1,
             "start": 0.0, "end": 5.0, "score": 0.9}
        ]
        with patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024), \
             patch("utils.pinecone_client.search_chunks",
                   return_value=fake_chunks), \
             patch("utils.groq_llm.get_answer",
                   return_value="Python is a general-purpose language."), \
             patch("chatbot.views._sarvam_tts") as mock_tts, \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            response = self.client.post(
                "/chatbot/query/",
                data=json.dumps({
                    "query": "what is python",
                    "course_id": self.course.id,
                    "voice": True,
                    "defer_voice": True,
                }),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["answer"], "Python is a general-purpose language.")
        self.assertTrue(body["voice_deferred"])
        self.assertNotIn("audio", body)
        mock_tts.assert_not_called()

    def test_malayalam_query_translates_to_english_then_back_to_malayalam(self):
        fake_chunks = [
            {"text": "Python is a language", "video_id": 1,
             "start": 0.0, "end": 5.0, "score": 0.9}
        ]
        with patch("chatbot.views._sarvam_translate") as mock_translate, \
             patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024) as mock_embed, \
             patch("utils.pinecone_client.search_chunks",
                   return_value=fake_chunks), \
             patch("utils.groq_llm.get_answer",
                   return_value="Python is a general-purpose language."), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            mock_translate.side_effect = [
                "What is Python?",
                "പൈത്തൺ ഒരു പൊതുവായ പ്രോഗ്രാമിംഗ് ഭാഷയാണ്.",
            ]
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({
                    "query": "പൈത്തൺ എന്താണ്?",
                    "course_id": self.course.id,
                    "language": "ml",
                }),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertEqual(body["language"], "ml")
        self.assertEqual(body["answer"], "പൈത്തൺ ഒരു പൊതുവായ പ്രോഗ്രാമിംഗ് ഭാഷയാണ്.")
        mock_embed.assert_called_once_with("What is Python?")
        self.assertEqual(mock_translate.call_count, 2)

    def test_cache_hit_skips_infra(self):
        """Redis cache hit must not call embedding/Pinecone/Groq."""
        with patch("utils.redis_cache.get_cached_result",
                   return_value="Cached answer about Python"), \
             patch("utils.embeddings.generate_query_embedding") as mock_embed, \
             patch("utils.pinecone_client.search_chunks") as mock_search:
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({"query": "what is python", "course_id": self.course.id}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 200)
        mock_embed.assert_not_called()
        mock_search.assert_not_called()
        body = json.loads(r.content)
        self.assertEqual(body["answer"], "Cached answer about Python")

    def test_tts_endpoint_returns_audio(self):
        with patch("chatbot.views._sarvam_tts", return_value="FAKE_AUDIO"):
            response = self.client.post(
                "/chatbot/tts/",
                data=json.dumps({
                    "text": "Python is a general-purpose language.",
                    "language": "en",
                }),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["audio"], "FAKE_AUDIO")

    def test_unauthenticated_redirects(self):
        """Unauthenticated request gets redirected to login page."""
        c = Client()
        r = c.post("/chatbot/query/",
                   data=json.dumps({"query": "hi", "course_id": self.course.id}),
                   content_type="application/json")
        self.assertEqual(r.status_code, 302)

    def test_instructor_cannot_use_learning_assistant(self):
        instructor_client = Client()
        instructor_client.login(username="teacherchatbot", password="pass123")

        response = instructor_client.post(
            "/chatbot/query/",
            data=json.dumps({"query": "hello", "course_id": self.course.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Learning Assistant", json.loads(response.content)["error"])

    def test_student_cannot_query_unenrolled_course(self):
        other_course = Course.objects.create(
            title="Restricted Course",
            instructor=self.instructor,
            is_active=True,
        )

        response = self.client.post(
            "/chatbot/query/",
            data=json.dumps({"query": "hello", "course_id": other_course.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(
        MODAL_TOKEN_ID='modal-token-id',
        MODAL_TOKEN_SECRET='modal-token-secret',
        CHATBOT_EMBEDDINGS_REMOTE_FIRST=True,
    )
    def test_query_uses_deployed_modal_lookup_when_remote_embeddings_enabled(self):
        import utils.embeddings as embeddings

        embeddings._model = None
        embeddings._remote_embedder = None
        embeddings._remote_failure_until = 0.0

        fake_chunks = [
            {"text": "Python is a language", "video_id": 1,
             "start": 0.0, "end": 5.0, "score": 0.9}
        ]
        remote_instance = MagicMock()
        remote_instance.generate.remote.return_value = [[0.1] * 1024]
        remote_cls = MagicMock(return_value=remote_instance)

        with patch("modal.Cls.from_name", return_value=remote_cls) as mock_from_name, \
             patch("utils.embeddings._local_encode", side_effect=AssertionError("local fallback should not run")), \
             patch("utils.pinecone_client.search_chunks", return_value=fake_chunks), \
             patch("utils.groq_llm.get_answer", return_value="Python is a general-purpose language."), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            response = self.client.post(
                "/chatbot/query/",
                data=json.dumps({"query": "what is python", "course_id": self.course.id}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        mock_from_name.assert_called_once_with("lms-transcription", "EmbeddingGenerator")
        remote_cls.assert_called_once_with()
        remote_instance.generate.remote.assert_called_once()


class GroqAnswerStyleTests(TestCase):
    def test_clean_answer_style_removes_lecture_content_lead_in(self):
        answer = "Based on the lecture content, Python is an interpreted language."
        self.assertEqual(
            _clean_answer_style(answer),
            "Python is an interpreted language.",
        )

    def test_clean_answer_style_removes_document_shared_lead_in(self):
        answer = "Based on the document shared: Variables store values."
        self.assertEqual(
            _clean_answer_style(answer),
            "Variables store values.",
        )


class LearningAssistantVisibilityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_visibility",
            password="pass123",
            role=User.Role.ADMIN,
        )
        self.instructor = User.objects.create_user(
            username="instructor_visibility",
            password="pass123",
            role=User.Role.INSTRUCTOR,
        )

    def test_admin_dashboard_does_not_show_learning_assistant(self):
        self.client.force_login(self.admin)

        response = self.client.get("/admin-dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Learning Assistant")

    def test_instructor_dashboard_does_not_show_learning_assistant(self):
        self.client.force_login(self.instructor)

        response = self.client.get("/instructor-dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Learning Assistant")


class LearningAssistantVoiceInputTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="voice_student",
            password="pass123",
            role=User.Role.STUDENT,
        )
        self.instructor = User.objects.create_user(
            username="voice_teacher",
            password="pass123",
            role=User.Role.INSTRUCTOR,
        )
        self.course = Course.objects.create(
            title="Voice Enabled Course",
            instructor=self.instructor,
            is_active=True,
        )
        Enrollment.objects.create(
            student=self.student,
            course=self.course,
            is_active=True,
        )

    def test_student_dashboard_shows_voice_input_button(self):
        self.client.force_login(self.student)

        response = self.client.get("/student-dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="chat-mic-btn"')
        self.assertContains(response, 'id="chat-cancel-btn"')
        self.assertContains(response, "Start voice input")
        self.assertContains(response, "chatAutoPlayAudio")
        self.assertContains(response, "chatCancelRequest")
        self.assertContains(response, "AbortController")
        self.assertContains(response, "language:  chatLang")
        self.assertContains(response, "Learning Assistant")
        self.assertContains(response, "chatOpenPanel(); return false;")
        self.assertContains(response, "function chatOpenPanel()")

    def test_student_dashboard_renders_chatbot_csrf_token_for_ajax(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)

        dashboard_response = csrf_client.get("/student-dashboard/")

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'id="chatbot-csrf-token"')
        self.assertContains(dashboard_response, 'name="csrfmiddlewaretoken"')

        html = dashboard_response.content.decode("utf-8")
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        self.assertIsNotNone(match)
        csrf_token = match.group(1)

        fake_chunks = [
            {"text": "Python is a language", "video_id": 1,
             "start": 0.0, "end": 5.0, "score": 0.9}
        ]
        with patch("utils.embeddings.generate_query_embedding",
                   return_value=[0.1] * 1024), \
             patch("utils.pinecone_client.search_chunks",
                   return_value=fake_chunks), \
             patch("utils.groq_llm.get_answer",
                   return_value="Python is a general-purpose language."), \
             patch("utils.redis_cache.get_cached_result", return_value=None), \
             patch("utils.redis_cache.set_cached_result"):
            query_response = csrf_client.post(
                "/chatbot/query/",
                data=json.dumps({
                    "query": "what is python",
                    "course_id": self.course.id,
                }),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )

        self.assertEqual(query_response.status_code, 200)
        self.assertEqual(
            json.loads(query_response.content)["answer"],
            "Python is a general-purpose language.",
        )
