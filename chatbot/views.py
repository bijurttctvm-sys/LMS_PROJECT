import base64
import logging
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

# Malayalam Unicode block: U+0D00 – U+0D7F
_ML_RE = re.compile(r"[ഀ-ൿ]")


def _is_malayalam(text: str) -> bool:
    return bool(_ML_RE.search(text))


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
            "input":                text,
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            "speaker_gender":       "Female",
            "mode":                 "formal",
            "model":                "mayura:v1",
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
            "inputs":               [text[:500]],
            "target_language_code": language,
            "speaker":              "anushka",
            "pitch":                0,
            "pace":                 1.0,
            "loudness":             1.5,
            "speech_sample_rate":   22050,
            "enable_preprocessing": True,
            "model":                "bulbul:v2",
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

    query      = body.get("query", "").strip()
    course_id  = body.get("course_id")
    want_voice = bool(body.get("voice", False))

    if not query:
        return JsonResponse({"error": "query is required"}, status=400)
    if not course_id:
        return JsonResponse({"error": "course_id is required"}, status=400)
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "course_id must be an integer"}, status=400)

    # Students must be enrolled in the course — admins and instructors have full access
    from users.models import User as _User
    if request.user.role == _User.Role.STUDENT:
        from courses.models import Enrollment
        enrolled = Enrollment.objects.filter(
            student=request.user, course_id=course_id, is_active=True
        ).exists()
        if not enrolled:
            return JsonResponse(
                {"error": "You are not enrolled in this course."},
                status=403,
            )

    # Step 1 — detect language
    query_is_malayalam = _is_malayalam(query)
    response_language  = "ml" if query_is_malayalam else "en"

    # Step 2 — if Malayalam → translate query to English for retrieval
    english_query = query
    if query_is_malayalam:
        try:
            english_query = _sarvam_translate(query, "ml-IN", "en-IN")
        except Exception as exc:
            logger.warning("Sarvam ML→EN translation failed: %s", exc)

    # Step 3 — check Redis cache (cache miss is silent on Redis-unavailable)
    from utils.redis_cache import get_cached_result, set_cached_result
    cache_key = f"{course_id}:{english_query}"
    cached    = get_cached_result(cache_key)

    source_chunks = []
    answer_en     = None

    if cached:
        logger.info("Cache HIT for course %s", course_id)
        answer_en = cached
    else:
        # Step 4 — generate query embedding (CPU, lazy-loaded model)
        try:
            from utils.embeddings import generate_query_embedding
            query_embedding = generate_query_embedding(english_query)
        except Exception as exc:
            logger.error("Embedding generation failed: %s", exc)
            return JsonResponse(
                {"error": "Embedding service unavailable", "detail": str(exc)},
                status=503,
            )

        # Step 5 — search Pinecone
        try:
            from utils.pinecone_client import search_chunks
            raw_chunks = search_chunks(query_embedding, course_id, top_k=5)
        except Exception as exc:
            logger.error("Pinecone search failed: %s", exc)
            return JsonResponse(
                {"error": "Vector search unavailable", "detail": str(exc)},
                status=503,
            )

        source_chunks = [
            {
                "text":      c["text"][:200],
                "video_id":  c["video_id"],
                "start":     c["start"],
                "end":       c["end"],
                "score":     round(c["score"], 4),
            }
            for c in raw_chunks
        ]

        # Step 6 — call Groq
        try:
            from utils.groq_llm import get_answer
            answer_en = get_answer(english_query, raw_chunks, language="en")
        except Exception as exc:
            logger.error("Groq LLM call failed: %s", exc)
            return JsonResponse(
                {"error": "LLM service unavailable", "detail": str(exc)},
                status=503,
            )

        # Step 7 — cache the English answer
        set_cached_result(cache_key, answer_en)

    # Step 8 — if Malayalam requested, translate answer back
    answer_final = answer_en
    if query_is_malayalam:
        try:
            answer_final = _sarvam_translate(answer_en, "en-IN", "ml-IN")
        except Exception as exc:
            logger.warning("Answer EN→ML translation failed: %s — returning EN", exc)

    # Step 9 — TTS if voice requested
    audio_b64 = None
    if want_voice:
        try:
            tts_lang  = "ml-IN" if query_is_malayalam else "en-IN"
            audio_b64 = _sarvam_tts(answer_final, language=tts_lang)
        except Exception as exc:
            logger.warning("TTS failed: %s", exc)

    payload = {
        "answer":        answer_final,
        "language":      response_language,
        "source_chunks": source_chunks,
    }
    if audio_b64:
        payload["audio"] = audio_b64

    return JsonResponse(payload)
