import logging
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)
_SUPPORTED_CHAT_LANGUAGES = {"en", "ml"}

# Malayalam Unicode block: U+0D00 - U+0D7F
_ML_RE = re.compile(r"[\u0D00-\u0D7F]")


def _is_malayalam(text: str) -> bool:
    return bool(_ML_RE.search(text))


def _normalise_chat_language(value) -> str:
    if not isinstance(value, str):
        return ""
    language = value.strip().lower()
    return language if language in _SUPPORTED_CHAT_LANGUAGES else ""


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


def _sarvam_translate(text: str, source_lang: str, target_lang: str) -> str:
    import requests
    from django.conf import settings

    resp = requests.post(
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
    return resp.json().get("translated_text", text)


def _sarvam_tts(text: str, language: str = "ml-IN") -> str:
    import requests
    from django.conf import settings

    resp = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": settings.SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "inputs": [text[:500]],
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
    return audios[0] if audios else ""


@login_required
@require_POST
def chatbot_query(request):
    import json

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    query = body.get("query", "").strip()
    course_id = body.get("course_id")
    want_voice = bool(body.get("voice", False))
    requested_language = _normalise_chat_language(body.get("language"))

    if not query:
        return JsonResponse({"error": "query is required"}, status=400)
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
        try:
            english_query = _sarvam_translate(query, "ml-IN", "en-IN")
        except Exception as exc:
            logger.warning("Sarvam ML->EN translation failed: %s", exc)

    from utils.redis_cache import get_cached_result, set_cached_result

    cache_key = f"{course_id}:{english_query}"
    cached = get_cached_result(cache_key)

    source_chunks = []
    answer_en = None

    if cached:
        logger.info("Cache HIT for course %s", course_id)
        answer_en = cached
    else:
        try:
            from utils.embeddings import generate_query_embedding

            query_embedding = generate_query_embedding(english_query)
        except Exception as exc:
            logger.exception("Embedding generation failed")
            return JsonResponse(
                {"error": "Embedding service unavailable"},
                status=503,
            )

        try:
            from utils.pinecone_client import search_chunks

            raw_chunks = search_chunks(query_embedding, course_id, top_k=5)
        except Exception as exc:
            logger.exception("Pinecone search failed")
            return JsonResponse(
                {"error": "Vector search unavailable"},
                status=503,
            )

        source_chunks = [
            {
                "text": c["text"][:200],
                "video_id": c["video_id"],
                "start": c["start"],
                "end": c["end"],
                "score": round(c["score"], 4),
            }
            for c in raw_chunks
        ]

        try:
            from utils.groq_llm import get_answer

            answer_en = get_answer(english_query, raw_chunks, language="en")
        except Exception as exc:
            logger.exception("Groq LLM call failed")
            return JsonResponse(
                {"error": "LLM service unavailable"},
                status=503,
            )

        set_cached_result(cache_key, answer_en)

    answer_final = answer_en
    if response_language == "ml":
        try:
            answer_final = _sarvam_translate(answer_en, "en-IN", "ml-IN")
        except Exception as exc:
            logger.warning("Answer EN->ML translation failed: %s; returning EN", exc)

    audio_b64 = None
    if want_voice:
        try:
            tts_lang = "ml-IN" if response_language == "ml" else "en-IN"
            audio_b64 = _sarvam_tts(answer_final, language=tts_lang)
        except Exception as exc:
            logger.warning("TTS failed: %s", exc)

    payload = {
        "answer": answer_final,
        "language": response_language,
        "source_chunks": source_chunks,
    }
    if audio_b64:
        payload["audio"] = audio_b64

    return JsonResponse(payload)
