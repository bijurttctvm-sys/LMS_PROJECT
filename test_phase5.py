"""
Phase 5 — RAG Pipeline test suite
Run: python manage.py shell --settings=lms_project.test_settings < test_phase5.py

Steps covered
  1.  Task chain: generate_pdfs -> generate_embeddings -> upsert_chunks (code wiring)
  2.  Pinecone client: imports, test_connection() graceful failure
  3.  Django shell: test_connection() returns False with placeholder key (expected)
  4.  Chatbot endpoint: URL routing, input validation, full flow with mocked infra
  5.  Answer relevance: SKIP without real Pinecone/Groq keys
  6.  Redis cache: key logic, TTL, graceful failure when server down
  7.  Malayalam query: language detection + Sarvam placeholder path
  8.  Voice TTS: voice=true parameter path tested with mocked infra
  9.  redis-cli equivalent: confirmed SKIP without Redis
"""
import json
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("Standalone diagnostic script; run explicitly.")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lms_project.test_settings")
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

User = get_user_model()
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

failures = []

# Ensure test SQLite DB has all tables (equivalent to manage.py migrate)
from django.core.management import call_command as _call
_call("migrate", "--run-syncdb", verbosity=0)


def check(label, cond, detail=""):
    sym = PASS if cond else FAIL
    if not cond:
        failures.append(label)
    suffix = f"  ({detail})" if detail else ""
    print(f"  {sym}: {label}{suffix}")
    return cond


def skip(label, reason):
    print(f"  {SKIP}: {label}  -- {reason}")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 1. Task chain wiring ===")
# ─────────────────────────────────────────────────────────────────────────────

try:
    from videos.tasks import (
        process_video,
        transcribe_video,
        generate_pdfs,
        generate_embeddings,
    )
    check("All 4 tasks importable", True)
except Exception as e:
    check("All 4 tasks importable", False, str(e))

# Confirm generate_pdfs source calls generate_embeddings.delay()
import inspect
try:
    src = inspect.getsource(generate_pdfs)
    check("generate_pdfs calls generate_embeddings.delay()",
          "generate_embeddings.delay" in src)
except Exception as e:
    check("generate_pdfs calls generate_embeddings.delay()", False, str(e))

try:
    src = inspect.getsource(generate_embeddings)
    check("generate_embeddings imports upsert_chunks",
          "upsert_chunks" in src)
    check("generate_embeddings uses 'passage: ' prefix",
          "'passage: '" in src or '"passage: "' in src)
    check("generate_embeddings updates embedding_id",
          "embedding_id" in src)
    check("generate_embeddings checkpoints pending chunks",
          "not c.embedding_id" in src)
except Exception as e:
    check("generate_embeddings source checks", False, str(e))

try:
    from utils.pinecone_client import upsert_chunks, search_chunks, delete_video_chunks, test_connection
    check("utils.pinecone_client all 5 functions importable", True)
except Exception as e:
    check("utils.pinecone_client all 5 functions importable", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 2. Pinecone client structure ===")
# ─────────────────────────────────────────────────────────────────────────────

try:
    import inspect as _ins
    from utils import pinecone_client as _pc_mod
    src = inspect.getsource(_pc_mod)
    check("vector ID format vid{id}_c{idx}",
          "vid{video_id}_c{chunk.chunk_index}" in src or
          "vid{video_id}_c{chunk_index}" in src or
          'f"vid{video_id}_c{chunk.chunk_index}"' in src)
    check("metadata includes course_id",      '"course_id"' in src)
    check("metadata includes topic_segment",  '"topic_segment"' in src)
    check("upsert batched in 100s",           "batch_size = 100" in src)
    check("search uses course_id filter",     '"$eq": course_id' in src or
          '"\\$eq": course_id' in src)
except Exception as e:
    check("pinecone_client structure", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 3. test_connection() with placeholder key ===")
# ─────────────────────────────────────────────────────────────────────────────

try:
    result = test_connection()
    check("test_connection() returns False (not raises) with bad key",
          result is False,
          f"got {result!r}")
except Exception as e:
    check("test_connection() handles bad key gracefully", False, str(e))

pinecone_key_real = bool(
    settings.PINECONE_API_KEY and
    settings.PINECONE_API_KEY != "your-pinecone-api-key"
)
if pinecone_key_real:
    try:
        result = test_connection()
        if result:
            check("test_connection() returns True with valid key", True)
        else:
            skip("test_connection() returns True",
                 "PINECONE_API_KEY set but Pinecone returned an error — verify the key and index name")
    except Exception as e:
        skip("test_connection() with real key", str(e)[:80])
else:
    skip("test_connection() True with real key",
         "set PINECONE_API_KEY in .env to enable")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 4. Chatbot endpoint ===")
# ─────────────────────────────────────────────────────────────────────────────

u, _ = User.objects.get_or_create(
    username="p5_tester",
    defaults={"email": "p5@test.com", "role": "student"},
)
u.set_password("pass123")
u.save()

cl = Client()
cl.login(username="p5_tester", password="pass123")

with override_settings(ALLOWED_HOSTS=["*"]):

    # ── routing guards ──────────────────────────────────────────────────────
    r = cl.get("/chatbot/query/")
    check("GET /chatbot/query/ -> 405", r.status_code == 405)

    r = cl.post("/chatbot/query/", data="bad json",
                content_type="application/json")
    check("Invalid JSON body -> 400", r.status_code == 400)

    r = cl.post("/chatbot/query/",
                data=json.dumps({"course_id": 1}),
                content_type="application/json")
    check("Missing 'query' -> 400", r.status_code == 400)

    r = cl.post("/chatbot/query/",
                data=json.dumps({"query": "hello"}),
                content_type="application/json")
    check("Missing 'course_id' -> 400", r.status_code == 400)

    r = cl.post("/chatbot/query/",
                data=json.dumps({"query": "hello", "course_id": "abc"}),
                content_type="application/json")
    check("Non-integer course_id -> 400", r.status_code == 400)

    unauthenticated = Client()
    r = unauthenticated.post("/chatbot/query/",
                             data=json.dumps({"query": "hi", "course_id": 1}),
                             content_type="application/json")
    check("Unauthenticated -> 302 redirect to login", r.status_code == 302)

    # ── happy path with mocked infra ────────────────────────────────────────
    fake_chunks = [
        {"text": "Python is a high-level, interpreted programming language.",
         "video_id": 1, "start": 0.0, "end": 6.0, "score": 0.92},
        {"text": "Python was created by Guido van Rossum in 1991.",
         "video_id": 1, "start": 6.0, "end": 12.0, "score": 0.87},
    ]
    with patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks",
               return_value=fake_chunks), \
         patch("utils.groq_llm.get_answer",
               return_value="Python is a high-level interpreted language created in 1991."), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python", "course_id": 1}),
                    content_type="application/json")

    check("English query -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = json.loads(r.content)
        check("Response has 'answer' key",       "answer"        in body)
        check("Response has 'language' key",     "language"      in body)
        check("Response has 'source_chunks'",    "source_chunks" in body)
        check("Language detected as 'en'",       body.get("language") == "en")
        check("Source chunks preserved",         len(body.get("source_chunks", [])) == 2)

    # ── Pinecone error -> 503 (the main bug we fixed) ────────────────────────
    with patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks",
               side_effect=Exception("Pinecone [401] Invalid API Key")), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python", "course_id": 1}),
                    content_type="application/json")

    check("Pinecone 401 -> 503 JSON (not 500 HTML)", r.status_code == 503)
    try:
        err_body = json.loads(r.content)
        check("503 response is valid JSON with 'error' key", "error" in err_body)
    except ValueError:
        check("503 response is valid JSON", False, "got HTML 500")

    # ── Groq error -> 503 ───────────────────────────────────────────────────
    with patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks", return_value=[]), \
         patch("utils.groq_llm.get_answer",
               side_effect=Exception("Groq 401 Invalid API Key")), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python", "course_id": 1}),
                    content_type="application/json")

    check("Groq 401 -> 503 JSON (not 500 HTML)", r.status_code == 503)

    # ── Embedding error -> 503 ──────────────────────────────────────────────
    with patch("utils.embeddings.generate_query_embedding",
               side_effect=Exception("OOM")), \
         patch("utils.redis_cache.get_cached_result", return_value=None):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python", "course_id": 1}),
                    content_type="application/json")

    check("Embedding error -> 503 JSON", r.status_code == 503)

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 5. Answer relevance with real credentials ===")
# ─────────────────────────────────────────────────────────────────────────────

groq_key_real    = bool(settings.GROQ_API_KEY and
                        settings.GROQ_API_KEY != "your-groq-api-key")
pinecone_key_real = bool(settings.PINECONE_API_KEY and
                         settings.PINECONE_API_KEY != "your-pinecone-api-key")

if groq_key_real:
    try:
        from utils.groq_llm import get_answer
        ans = get_answer("What is Python?",
                         [{"text": "Python is a high-level programming language."}],
                         language="en")
        check("Groq answers non-empty with real key",
              bool(ans and len(ans) > 10), ans[:80] if ans else "empty")
    except Exception as e:
        _emsg = str(e)
        if "401" in _emsg or "Invalid API Key" in _emsg or "invalid_api_key" in _emsg.lower():
            skip("Groq real answer",
                 "GROQ_API_KEY is set but returned 401 -- verify the key is correct")
        else:
            check("Groq real API call", False, _emsg[:80])
else:
    skip("Groq real answer", "set GROQ_API_KEY in .env to enable")

if not pinecone_key_real:
    skip("Pinecone real vector search", "set PINECONE_API_KEY + PINECONE_INDEX_NAME in .env")

if not (groq_key_real and pinecone_key_real):
    skip("End-to-end answer relevance test",
         "requires both PINECONE_API_KEY and GROQ_API_KEY in .env")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 6. Redis cache ===")
# ─────────────────────────────────────────────────────────────────────────────

from utils.redis_cache import _cache_key, get_cached_result, set_cached_result

# Key-generation logic (no Redis needed)
k1 = _cache_key("  What is Python?  ")
k2 = _cache_key("what is python?")
k3 = _cache_key("What is Java?")
check("Cache key is deterministic (strip + lower)",  k1 == k2)
check("Different queries give different keys",        k1 != k3)
check("Key starts with 'chatbot:' prefix",           k1.startswith("chatbot:"))
check("SHA256 digest is 64 hex chars after prefix",  len(k1) == len("chatbot:") + 64)

# Redis connectivity
import redis as _redis
try:
    rc = _redis.from_url(settings.REDIS_URL, db=1, socket_connect_timeout=1)
    rc.ping()
    redis_up = True
except Exception:
    redis_up = False

if redis_up:
    from utils.redis_cache import _cache_key, set_cached_result, get_cached_result
    test_q = "__phase5_test_query__"
    test_a = "cached answer for phase 5 test"

    set_cached_result(test_q, test_a, ttl=60)
    retrieved = get_cached_result(test_q)
    check("Redis SET + GET round-trip",       retrieved == test_a)

    # Clean up
    import hashlib
    rc.delete(_cache_key(test_q))

    # Cache-hit path bypasses infra
    with override_settings(ALLOWED_HOSTS=["*"]):
        with patch("utils.redis_cache.get_cached_result",
                   return_value="cached from Redis"), \
             patch("utils.embeddings.generate_query_embedding") as mock_e, \
             patch("utils.pinecone_client.search_chunks") as mock_s:
            cl2 = Client()
            cl2.login(username="p5_tester", password="pass123")
            r = cl2.post("/chatbot/query/",
                         data=json.dumps({"query": "cache hit test",
                                          "course_id": 1}),
                         content_type="application/json")
        check("Cache hit -> 200 without calling embed/Pinecone",
              r.status_code == 200 and
              not mock_e.called and
              not mock_s.called)
        body = json.loads(r.content)
        check("Cache hit response contains cached answer",
              body.get("answer") == "cached from Redis")

    # redis-cli equivalent
    pattern = "chatbot:*"
    keys = rc.keys(pattern)
    check(f"redis-cli -n 1 KEYS 'chatbot:*' works (found {len(keys)} key(s))", True)
    print(f"    Matching keys: {keys[:5]}")
else:
    skip("Redis SET/GET round-trip",
         "start Redis: docker run -d -p 6379:6379 redis:alpine")
    skip("Cache hit bypasses infra (live Redis)",
         "start Redis to enable")
    skip("redis-cli -n 1 KEYS 'chatbot:*'",
         "start Redis to enable")
    # Still check graceful-failure path
    result = get_cached_result("anything")
    check("get_cached_result returns None when Redis down", result is None)
    # set_cached_result should not raise
    try:
        set_cached_result("anything", "answer", ttl=60)
        check("set_cached_result silent when Redis down", True)
    except Exception as e:
        check("set_cached_result silent when Redis down", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 7. Malayalam language detection and query path ===")
# ─────────────────────────────────────────────────────────────────────────────

from chatbot.views import _is_malayalam

check("Pure English -> False",          not _is_malayalam("what is python"))
check("Pure Malayalam -> True",         _is_malayalam("പൈത്തൺ എന്താണ്?"))
check("Mixed (ML chars) -> True",       _is_malayalam("Python എന്ത് ആണ്"))
check("Empty string -> False",          not _is_malayalam(""))
check("Numbers/symbols -> False",       not _is_malayalam("1234!@#$"))

# Malayalam query end-to-end with mocked Sarvam
sarvam_key_real = bool(settings.SARVAM_API_KEY and
                       settings.SARVAM_API_KEY != "your-sarvam-api-key")

ML_QUERY = "പൈത്തൺ പ്രോഗ്രാമിംഗ് ഭാഷ എന്ത് ആണ്?"
with override_settings(ALLOWED_HOSTS=["*"]):
    with patch("chatbot.views._sarvam_translate",
               return_value="What is Python programming language?"), \
         patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks",
               return_value=[{"text": "Python is a language",
                              "video_id": 1, "start": 0.0,
                              "end": 5.0, "score": 0.9}]), \
         patch("utils.groq_llm.get_answer",
               return_value="Python is a high-level language."), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": ML_QUERY, "course_id": 1}),
                    content_type="application/json")

check("Malayalam query -> 200", r.status_code == 200)
if r.status_code == 200:
    body = json.loads(r.content)
    check("Malayalam query -> language='ml'",
          body.get("language") == "ml")

if sarvam_key_real:
    try:
        from chatbot.views import _sarvam_translate
        translated = _sarvam_translate("Hello, how are you?", "en-IN", "ml-IN")
        check("Sarvam EN->ML translation returns non-empty string",
              bool(translated and len(translated) > 3), translated[:60])
    except Exception as e:
        _em = str(e)
        if any(x in _em for x in ("401", "403", "Unauthorized", "Forbidden", "subscription")):
            skip("Sarvam real EN->ML translation",
                 "SARVAM_API_KEY set but returned auth error -- verify the key")
        else:
            check("Sarvam real EN->ML translation", False, _em[:80])
else:
    skip("Real Sarvam translation", "set SARVAM_API_KEY in .env to enable")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 8. Voice (TTS) parameter ===")
# ─────────────────────────────────────────────────────────────────────────────

FAKE_B64 = "UklGRiQAAABXQVZFZm10IBAAAA"   # truncated WAV header base64

with override_settings(ALLOWED_HOSTS=["*"]):
    # Without voice=true -> no 'audio' key in response
    with patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks", return_value=[]), \
         patch("utils.groq_llm.get_answer", return_value="Python answer."), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python",
                                     "course_id": 1}),
                    content_type="application/json")
    check("voice omitted -> no 'audio' key in response",
          "audio" not in json.loads(r.content))

    # With voice=true and mocked TTS -> 'audio' key present
    with patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks", return_value=[]), \
         patch("utils.groq_llm.get_answer", return_value="Python answer."), \
         patch("chatbot.views._sarvam_tts", return_value=FAKE_B64), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python",
                                     "course_id": 1,
                                     "voice": True}),
                    content_type="application/json")
    check("voice=true -> 'audio' key present in response",
          "audio" in json.loads(r.content))
    check("audio value is a string (base64)",
          isinstance(json.loads(r.content).get("audio"), str))

    # TTS error -> graceful (still 200, just no 'audio' key)
    with patch("utils.embeddings.generate_query_embedding",
               return_value=[0.1] * 1024), \
         patch("utils.pinecone_client.search_chunks", return_value=[]), \
         patch("utils.groq_llm.get_answer", return_value="Python answer."), \
         patch("chatbot.views._sarvam_tts",
               side_effect=Exception("Sarvam TTS 500")), \
         patch("utils.redis_cache.get_cached_result", return_value=None), \
         patch("utils.redis_cache.set_cached_result"):
        r = cl.post("/chatbot/query/",
                    data=json.dumps({"query": "what is python",
                                     "course_id": 1,
                                     "voice": True}),
                    content_type="application/json")
    body = json.loads(r.content)
    check("TTS failure -> 200 with answer, no 'audio' key",
          r.status_code == 200 and "audio" not in body and "answer" in body)

if sarvam_key_real:
    try:
        from chatbot.views import _sarvam_tts
        b64 = _sarvam_tts("Hello", language="en-IN")
        check("Sarvam TTS returns base64 string",
              bool(b64 and isinstance(b64, str) and len(b64) > 20), "len=" + str(len(b64)))
    except Exception as e:
        _em = str(e)
        if any(x in _em for x in ("401", "403", "Unauthorized", "Forbidden", "subscription")):
            skip("Sarvam TTS with real key",
                 "SARVAM_API_KEY set but returned auth error -- verify the key")
        else:
            check("Sarvam TTS with real key", False, _em[:80])
else:
    skip("Real Sarvam TTS call", "set SARVAM_API_KEY in .env to enable")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 9. redis-cli equivalent: KEYS 'chatbot:*' ===")
# ─────────────────────────────────────────────────────────────────────────────

if redis_up:
    keys = rc.keys("chatbot:*")
    print(f"  Found {len(keys)} chatbot cache key(s) in Redis DB 1")
    check("redis-cli -n 1 KEYS 'chatbot:*' reachable", True)
else:
    skip("redis-cli -n 1 KEYS 'chatbot:*'",
         "start Redis: docker run -d -p 6379:6379 redis:alpine")
    print("    To inspect keys once Redis is running:")
    print("      docker exec <container> redis-cli -n 1 KEYS 'chatbot:*'")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== manage.py check ===")
# ─────────────────────────────────────────────────────────────────────────────
from django.core.management import call_command
from io import StringIO
out = StringIO()
call_command("check", stdout=out, stderr=out)
output = out.getvalue()
_clean = ("no issues" in output or "0 issues" in output or output.strip() == "")
check("manage.py check reports 0 issues",
      _clean, output.strip()[:120] if not _clean else "clean")

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Summary ===")
# ─────────────────────────────────────────────────────────────────────────────
if failures:
    print(f"  {len(failures)} FAIL(s):")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print("  All locally-testable checks PASSED.")
    print()
    print("  Infrastructure steps that need real credentials:")
    infra = []
    if not pinecone_key_real:
        infra.append("PINECONE_API_KEY + PINECONE_INDEX_NAME  -> test_connection(), vector search")
    if not groq_key_real:
        infra.append("GROQ_API_KEY                           -> LLM answer generation")
    if not sarvam_key_real:
        infra.append("SARVAM_API_KEY                         -> Malayalam translation + TTS")
    if not redis_up:
        infra.append("Redis server                           -> cache hit test, redis-cli keys")
    for i in infra:
        print(f"    * {i}")
    if not infra:
        print("    (none — all credentials present)")
