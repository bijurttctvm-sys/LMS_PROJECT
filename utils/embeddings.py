import logging

logger = logging.getLogger(__name__)

_tokenizer = None
_model = None
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


def _encode(prefixed_texts: list) -> list:
    """Encode a list of already-prefixed texts, return list of float lists."""
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
            attn_mask  = inputs["attention_mask"].unsqueeze(-1).float()
            mean_emb   = (token_embs * attn_mask).sum(1) / attn_mask.sum(1)
            normalised = F.normalize(mean_emb, p=2, dim=1)
        all_embeddings.extend(normalised.tolist())
    return all_embeddings


def generate_query_embedding(text: str) -> list:
    """Return a 1024-dim L2-normalised embedding for a single query string."""
    return _encode([f"query: {text}"])[0]


def generate_passage_embeddings(texts: list) -> list:
    """
    Return 1024-dim L2-normalised embeddings for a list of passage strings.
    Adds the required 'passage: ' prefix automatically.
    Processes in batches of _BATCH_SIZE to stay within CPU RAM.
    """
    prefixed = [f"passage: {t}" for t in texts]
    return _encode(prefixed)
