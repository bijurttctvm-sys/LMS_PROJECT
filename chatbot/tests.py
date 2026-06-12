import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

User = get_user_model()


class ChatbotQueryViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testchatbot", email="tc@test.com",
            password="pass123", role="student"
        )
        self.client.login(username="testchatbot", password="pass123")

    def test_get_returns_405(self):
        r = self.client.get("/chatbot/query/")
        self.assertEqual(r.status_code, 405)

    def test_missing_query_returns_400(self):
        r = self.client.post("/chatbot/query/",
                             data=json.dumps({"course_id": 1}),
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
                data=json.dumps({"query": "what is python", "course_id": 1}),
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
                data=json.dumps({"query": "hello", "course_id": 1}),
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
                data=json.dumps({"query": "what is python", "course_id": 1}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertIn("answer", body)
        self.assertIn("language", body)
        self.assertIn("source_chunks", body)
        self.assertEqual(body["language"], "en")

    def test_cache_hit_skips_infra(self):
        """Redis cache hit must not call embedding/Pinecone/Groq."""
        with patch("utils.redis_cache.get_cached_result",
                   return_value="Cached answer about Python"), \
             patch("utils.embeddings.generate_query_embedding") as mock_embed, \
             patch("utils.pinecone_client.search_chunks") as mock_search:
            r = self.client.post(
                "/chatbot/query/",
                data=json.dumps({"query": "what is python", "course_id": 1}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 200)
        mock_embed.assert_not_called()
        mock_search.assert_not_called()
        body = json.loads(r.content)
        self.assertEqual(body["answer"], "Cached answer about Python")

    def test_unauthenticated_redirects(self):
        """Unauthenticated request gets redirected to login page."""
        c = Client()
        r = c.post("/chatbot/query/",
                   data=json.dumps({"query": "hi", "course_id": 1}),
                   content_type="application/json")
        self.assertEqual(r.status_code, 302)
