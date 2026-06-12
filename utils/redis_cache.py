import hashlib
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

CACHE_PREFIX = "chatbot:"
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    import redis

    # Use DB 1 to stay separate from the Celery broker (DB 0)
    url = getattr(settings, "REDIS_CACHE_URL", None) or settings.REDIS_URL
    # Force DB 1 regardless of what the URL says
    _client = redis.from_url(url, db=1, decode_responses=True)
    return _client


def _cache_key(query_text: str) -> str:
    normalised = query_text.strip().lower()
    digest = hashlib.sha256(normalised.encode()).hexdigest()
    return f"{CACHE_PREFIX}{digest}"


def get_cached_result(query_text: str):
    """Return cached answer string, or None on miss / Redis unavailable."""
    try:
        return _get_client().get(_cache_key(query_text))
    except Exception as exc:
        logger.warning("Redis cache GET failed: %s", exc)
        return None


def set_cached_result(query_text: str, answer: str, ttl: int = 86400):
    """Store answer in Redis with a TTL (default 24 h)."""
    try:
        _get_client().setex(_cache_key(query_text), ttl, answer)
    except Exception as exc:
        logger.warning("Redis cache SET failed: %s", exc)
