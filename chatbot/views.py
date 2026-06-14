import json
import logging
import re
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)
_SUPPORTED_CHAT_LANGUAGES = {"en", "ml"}
_WHITESPACE_RE = re.compile(r"\s+")

# Malayalam Unicode block: U+0D00 - U+0D7F
_ML_RE = re.compile(r"[\u0D00-\u0D7F]")
_sarvam_session = None


def _is_malayalam(text: str) -> bool:
    return bool(_ML_RE.search(text))


def _normalise_chat_language(value) -> str:
    if not isinstance(value, str):
        return ""
    language = value.strip().lower()
    return language if language in _SUPPORTED_CHAT_LANGUAGES else ""


def _normalise_chat_text(value) -> str:
    if not isinstance(value, str):
        return ""
    return _WHITESPACE_RE.sub(" ", value).strip()


def _student_can_query_course(user, course_id: int) -> bool:
    from courses.models import Enrollment
    from users.models import User

    if user.role != User.Role.STUDENT:
        return False

    return Enrollment.objects.filter(
        student=user,
        course_id=course_id,
        is_active=True,
    ).exists()


def _can_use_learning_assistant(user) -> bool:
    from users.models import User

    return bool(user and user.is_authenticated and user.role == User.Role.STUDENT)


def _sarvam_http_session():
    global _sarvam_session
    if _sarvam_session is not None:
        return _sarvam_session

    import requests

    _sarvam_session = requests.Session()
    return _sarvam_session


def _sarvam_translate(text: str, source_lang: str, target_lang: str) -> str:
    from utils.redis_cache import get_cached_value, set_cached_value

    cache_key = f"{source_lang}:{target_lang}:{text}"
    cached = get_cached_value("chatbot-translate", cache_key)
    if cached:
        return cached

    resp = _sarvam_http_session().post(
        "https://api.sarvam.ai/translate",
        headers={
            "api-subscription-key": settings.SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "input": text,
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            "speaker_gender": "Female",
            "mode": "formal",
            "model": "mayura:v1",
        },
        timeout=30,
    )
    resp.raise_for_status()
    translated = resp.json().get("translated_text", text)
    if translated:
        set_cached_value(
            "chatbot-translate",
            cache_key,
            translated,
            ttl=getattr(settings, "CHATBOT_TRANSLATION_CACHE_TTL", 86400),
        )
    return translated


def _sarvam_tts(text: str, language: str = "ml-IN") -> str:
    from utils.redis_cache import get_cached_value, set_cached_value

    clipped_text = (text or "")[:500]
    cache_key = f"{language}:{clipped_text}"
    cached = get_cached_value("chatbot-tts", cache_key)
    if cached:
        return cached

    resp = _sarvam_http_session().post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": settings.SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "inputs": [clipped_text],
            "target_language_code": language,
            "speaker": "anushka",
            "pitch": 0,
            "pace": 1.0,
            "loudness": 1.5,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": "bulbul:v2",
        },
        timeout=30,
    )
    resp.raise_for_status()
    audios = resp.json().get("audios", [])
    audio_b64 = audios[0] if audios else ""
    if audio_b64:
        set_cached_value(
            "chatbot-tts",
            cache_key,
            audio_b64,
            ttl=getattr(settings, "CHATBOT_TTS_CACHE_TTL", 86400),
        )
    return audio_b64


def _build_source_chunks(raw_chunks):
    return [
        {
            "text": c["text"][:200],
            "video_id": c["video_id"],
            "start": c["start"],
            "end": c["end"],
            "score": round(c["score"], 4),
        }
        for c in raw_chunks
    ]


@login_required
@require_POST
def chatbot_query(request):
    started = time.perf_counter()
    timings = {}
    if request.content_type != 'application/json':
        return JsonResponse({"error": "Content-Type must be application/json"}, status=400)

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    query = _normalise_chat_text(body.get("query", ""))
    course_id = body.get("course_id")
    want_voice = bool(body.get("voice", False))
    defer_voice = bool(body.get("defer_voice", False))
    requested_language = _normalise_chat_language(body.get("language"))
    max_query_chars = max(50, int(getattr(settings, 'CHATBOT_MAX_QUERY_CHARS', 1000)))

    if not query:
        return JsonResponse({"error": "query is required"}, status=400)
    if len(query) > max_query_chars:
        return JsonResponse(
            {"error": f"query must be {max_query_chars} characters or fewer"},
            status=400,
        )
    if not course_id:
        return JsonResponse({"error": "course_id is required"}, status=400)
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "course_id must be an integer"}, status=400)
    if not _student_can_query_course(request.user, course_id):
        return JsonResponse(
            {
                "error": (
                    "Learning Assistant is available only to students "
                    "for their enrolled courses."
                )
            },
            status=403,
        )

    query_is_malayalam = _is_malayalam(query)
    response_language = "ml" if (requested_language == "ml" or query_is_malayalam) else "en"

    english_query = query
    if query_is_malayalam:
        stage_started = time.perf_counter()
        try:
            english_query = _sarvam_translate(query, "ml-IN", "en-IN")
        except Exception as exc:
            logger.warning("Sarvam ML->EN translation failed: %s", exc)
        timings["query_translate_sec"] = round(time.perf_counter() - stage_started, 3)

    from utils.redis_cache import get_cached_result, set_cached_result

    cache_key = f"{course_id}:{english_query}"
    stage_started = time.perf_counter()
    cached = get_cached_result(cache_key)
    timings["cache_lookup_sec"] = round(time.perf_counter() - stage_started, 3)

    source_chunks = []
    answer_en = None
    cache_hit = bool(cached)

    if cached:
        logger.info("Chatbot cache hit for course %s", course_id)
        answer_en = cached
    else:
        try:
            from utils.embeddings import generate_query_embedding

            stage_started = time.perf_counter()
            query_embedding = generate_query_embedding(english_query)
            timings["embedding_sec"] = round(time.perf_counter() - stage_started, 3)
        except Exception:
            logger.exception("Embedding generation failed")
            return JsonResponse(
                {"error": "Embedding service unavailable"},
                status=503,
            )

        try:
            from utils.pinecone_client import search_chunks

            stage_started = time.perf_counter()
            raw_chunks = search_chunks(
                query_embedding,
                course_id,
                top_k=max(1, int(getattr(settings, "CHATBOT_TOP_K", 3))),
            )
            timings["vector_search_sec"] = round(time.perf_counter() - stage_started, 3)
        except Exception:
            logger.exception("Pinecone search failed")
            return JsonResponse(
                {"error": "Vector search unavailable"},
                status=503,
            )

        source_chunks = _build_source_chunks(raw_chunks)

        try:
            from utils.groq_llm import get_answer

            stage_started = time.perf_counter()
            answer_en = get_answer(english_query, raw_chunks, language="en")
            timings["llm_sec"] = round(time.perf_counter() - stage_started, 3)
        except Exception:
            logger.exception("Groq LLM call failed")
            return JsonResponse(
                {"error": "LLM service unavailable"},
                status=503,
            )

        stage_started = time.perf_counter()
        set_cached_result(cache_key, answer_en)
        timings["cache_store_sec"] = round(time.perf_counter() - stage_started, 3)

    answer_final = answer_en
    if response_language == "ml":
        stage_started = time.perf_counter()
        try:
            answer_final = _sarvam_translate(answer_en, "en-IN", "ml-IN")
        except Exception as exc:
            logger.warning("Answer EN->ML translation failed: %s; returning EN", exc)
        timings["answer_translate_sec"] = round(time.perf_counter() - stage_started, 3)

    audio_b64 = None
    voice_deferred = bool(want_voice and defer_voice)
    if want_voice and not voice_deferred:
        stage_started = time.perf_counter()
        try:
            tts_lang = "ml-IN" if response_language == "ml" else "en-IN"
            audio_b64 = _sarvam_tts(answer_final, language=tts_lang)
        except Exception as exc:
            logger.warning("TTS failed: %s", exc)
        timings["tts_sec"] = round(time.perf_counter() - stage_started, 3)

    payload = {
        "answer": answer_final,
        "language": response_language,
        "source_chunks": source_chunks,
    }
    if audio_b64:
        payload["audio"] = audio_b64
    if voice_deferred:
        payload["voice_deferred"] = True

    timings["total_sec"] = round(time.perf_counter() - started, 3)
    logger.info(
        "Chatbot query completed course=%s cache_hit=%s language=%s voice=%s deferred=%s timings=%s",
        course_id,
        cache_hit,
        response_language,
        want_voice,
        voice_deferred,
        timings,
    )
    return JsonResponse(payload)


@login_required
@require_POST
def chatbot_tts(request):
    if not _can_use_learning_assistant(request.user):
        return JsonResponse(
            {"error": "Learning Assistant voice reply is available only to students."},
            status=403,
        )
    if request.content_type != 'application/json':
        return JsonResponse({"error": "Content-Type must be application/json"}, status=400)

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    text = _normalise_chat_text(body.get("text", ""))
    requested_language = _normalise_chat_language(body.get("language")) or "en"
    max_tts_chars = max(50, int(getattr(settings, 'CHATBOT_MAX_TTS_CHARS', 500)))

    if not text:
        return JsonResponse({"error": "text is required"}, status=400)
    if len(text) > max_tts_chars:
        return JsonResponse(
            {"error": f"text must be {max_tts_chars} characters or fewer"},
            status=400,
        )

    try:
        tts_lang = "ml-IN" if requested_language == "ml" else "en-IN"
        audio_b64 = _sarvam_tts(text, language=tts_lang)
    except Exception:
        logger.exception("Deferred TTS failed")
        return JsonResponse({"error": "Voice service unavailable"}, status=503)

    if not audio_b64:
        return JsonResponse({"error": "Voice reply unavailable"}, status=503)

    return JsonResponse({
        "audio": audio_b64,
        "language": requested_language,
    })
