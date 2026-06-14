import logging
import threading
import time

logger = logging.getLogger(__name__)

_tokenizer = None
_model = None
_remote_embedder = None
_warmup_started = False
_remote_failure_until = 0.0
_MODEL_ID = "intfloat/multilingual-e5-large"
_BATCH_SIZE = 16   # safe for CPU RAM


def _load():
    global _tokenizer, _model
    if _model is not None:
        return
    import torch
    from transformers import AutoModel, AutoTokenizer

    logger.info("Loading %s on CPU (first request only)", _MODEL_ID)
    _tokenizer = AutoTokenizer.from_pretrained(_MODEL_ID)
    _model = AutoModel.from_pretrained(_MODEL_ID)
    _model.eval()
    logger.info("Embedding model loaded")


def _local_encode(prefixed_texts: list) -> list:
    _load()
    import torch
    import torch.nn.functional as F

    all_embeddings = []
    for i in range(0, len(prefixed_texts), _BATCH_SIZE):
        batch = prefixed_texts[i : i + _BATCH_SIZE]
        inputs = _tokenizer(
            batch,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        with torch.no_grad():
            outputs = _model(**inputs)
            token_embs = outputs.last_hidden_state
            attn_mask = inputs["attention_mask"].unsqueeze(-1).float()
            mean_emb = (token_embs * attn_mask).sum(1) / attn_mask.sum(1)
            normalised = F.normalize(mean_emb, p=2, dim=1)
        all_embeddings.extend(normalised.tolist())
    return all_embeddings


def _modal_remote_available():
    from django.conf import settings

    if not getattr(settings, "CHATBOT_EMBEDDINGS_REMOTE_FIRST", True):
        return False
    return bool(
        getattr(settings, "MODAL_TOKEN_ID", "")
        and getattr(settings, "MODAL_TOKEN_SECRET", "")
    )


def _remote_cooldown_active():
    return time.monotonic() < _remote_failure_until


def _mark_remote_failure():
    global _remote_failure_until

    from django.conf import settings

    cooldown = max(1, int(getattr(settings, "CHATBOT_REMOTE_FAILURE_COOLDOWN", 60)))
    _remote_failure_until = time.monotonic() + cooldown


def _get_remote_embedder():
    global _remote_embedder

    if _remote_embedder is not None:
        return _remote_embedder

    from modal_functions.transcribe import EmbeddingGenerator

    _remote_embedder = EmbeddingGenerator()
    return _remote_embedder


def _remote_encode(prefixed_texts: list) -> list:
    embedder = _get_remote_embedder()
    return embedder.generate.remote(prefixed_texts)


def _encode(prefixed_texts: list) -> list:
    """Encode a list of already-prefixed texts, return list of float lists."""
    return _local_encode(prefixed_texts)


def _encode_query(prefixed_texts: list) -> list:
    if _model is not None:
        started = time.perf_counter()
        embeddings = _local_encode(prefixed_texts)
        logger.info(
            "Query embeddings generated via warmed local CPU in %.3fs",
            time.perf_counter() - started,
        )
        return embeddings

    if _modal_remote_available() and not _remote_cooldown_active():
        try:
            started = time.perf_counter()
            embeddings = _remote_encode(prefixed_texts)
            logger.info(
                "Query embeddings generated via Modal in %.3fs",
                time.perf_counter() - started,
            )
            return embeddings
        except Exception as exc:
            _mark_remote_failure()
            logger.warning("Modal query embedding unavailable: %s", exc)

    started = time.perf_counter()
    embeddings = _local_encode(prefixed_texts)
    logger.info(
        "Query embeddings generated via local CPU in %.3fs",
        time.perf_counter() - started,
    )
    return embeddings


def warm_up_embeddings():
    try:
        _local_encode(["query: warmup"])
        logger.info("Learning Assistant embedding warmup completed")
    except Exception as exc:
        logger.warning("Learning Assistant embedding warmup failed: %s", exc)


def warm_up_embeddings_async():
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True
    thread = threading.Thread(target=warm_up_embeddings, name="chatbot-embed-warmup", daemon=True)
    thread.start()


def generate_query_embedding(text: str) -> list:
    """Return a 1024-dim L2-normalised embedding for a single query string."""
    return _encode_query([f"query: {text}"])[0]


def generate_passage_embeddings(texts: list) -> list:
    """
    Return 1024-dim L2-normalised embeddings for a list of passage strings.
    Adds the required 'passage: ' prefix automatically.
    Processes in batches of _BATCH_SIZE to stay within CPU RAM.
    """
    prefixed = [f"passage: {t}" for t in texts]
    return _encode(prefixed)
