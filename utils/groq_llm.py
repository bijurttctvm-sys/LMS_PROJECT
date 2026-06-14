import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

_GROQ_MODEL = "llama-3.1-8b-instant"
_groq_client = None

_SYSTEM_EN = (
    "You are a helpful teaching assistant for an online lecture course. "
    "Answer questions clearly and concisely using only the provided course context. "
    "Do not begin answers with phrases like 'Based on the lecture content', "
    "'Based on the document shared', or similar lead-ins. "
    "Answer directly. "
    "Keep answers brief and focused unless the question clearly needs more detail. "
    "If the answer is not in the content, say so honestly."
)

_SYSTEM_ML = (
    "à´¨à´¿à´™àµà´™à´³àµâ€ à´’à´°àµ à´“à´£àµâ€à´²àµˆà´¨àµâ€ à´²à´•àµà´šà´°àµâ€ à´•àµ‹à´´àµâ€Œà´¸à´¿à´¨àµà´³àµà´³ à´¸à´¹à´¾à´¯à´•à´°à´®à´¾à´¯ à´…à´§àµà´¯à´¾à´ªà´¨ à´¸à´¹à´¾à´¯à´•à´¨à´¾à´£àµ. "
    "à´¨à´²àµâ€à´•à´¿à´¯ à´²à´•àµà´šà´°àµâ€ à´‰à´³àµà´³à´Ÿà´•àµà´•à´¤àµà´¤àµ† à´…à´Ÿà´¿à´¸àµà´¥à´¾à´¨à´®à´¾à´•àµà´•à´¿ à´®à´¾à´¤àµà´°à´‚ à´šàµ‹à´¦àµà´¯à´™àµà´™à´³àµâ€à´•àµà´•àµ à´‰à´¤àµà´¤à´°à´‚ à´¨à´²àµâ€à´•àµà´•."
)

_NO_CONTEXT_ML = (
    "à´ˆ à´šàµ‹à´¦àµà´¯à´¤àµà´¤à´¿à´¨àµ à´‰à´¤àµà´¤à´°à´‚ à´¨à´²àµâ€à´•à´¾à´¨àµâ€ à´•àµ‹à´´àµâ€Œà´¸àµ à´®àµ†à´±àµà´±àµ€à´°à´¿à´¯à´²à´¿à´²àµâ€ à´¨à´¿à´¨àµà´¨àµ à´ªàµà´°à´¸à´•àµà´¤à´®à´¾à´¯ à´µà´¿à´µà´°à´™àµà´™à´³àµâ€ à´²à´­àµà´¯à´®à´²àµà´²."
)

_LEADING_SOURCE_CLAUSES_RE = re.compile(
    r"^\s*(?:"
    r"based on (?:the )?(?:lecture|course) content"
    r"|based on (?:the )?(?:document|documents) shared"
    r"|based on (?:the )?shared document"
    r"|according to (?:the )?(?:lecture|course) content"
    r"|according to (?:the )?(?:document|documents) shared"
    r")\s*[:,.-]?\s*",
    re.IGNORECASE,
)


def _clean_answer_style(answer: str) -> str:
    cleaned = _LEADING_SOURCE_CLAUSES_RE.sub("", answer or "", count=1).strip()
    return cleaned or (answer or "").strip()


def _get_groq_client():
    global _groq_client
    if _groq_client is not None:
        return _groq_client

    from groq import Groq

    _groq_client = Groq(api_key=settings.GROQ_API_KEY)
    return _groq_client


def _trim_context_chunks(context_chunks):
    max_chunks = max(1, int(getattr(settings, "CHATBOT_MAX_CONTEXT_CHUNKS", 3)))
    max_chars = max(200, int(getattr(settings, "CHATBOT_CONTEXT_CHARS_PER_CHUNK", 700)))

    trimmed = []
    for chunk in (context_chunks or [])[:max_chunks]:
        text = " ".join((chunk.get("text") or "").split())
        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0].strip()
        trimmed.append({**chunk, "text": text})
    return trimmed


def get_answer(question: str, context_chunks: list, language: str = "en") -> str:
    """
    Build a RAG prompt from retrieved chunks and call Groq.

    context_chunks : list of dicts with at least a 'text' key
    language       : 'en' or 'ml'
    Returns the answer string.
    """
    if not context_chunks:
        return (
            "I could not find relevant information in the course material to answer that question."
            if language == "en"
            else _NO_CONTEXT_ML
        )

    context_chunks = _trim_context_chunks(context_chunks)
    context = "\n\n".join(
        f"[Chunk {i + 1}] {c['text']}" for i, c in enumerate(context_chunks)
    )

    if language == "ml":
        user_message = (
            f"à´‡à´¨à´¿à´ªàµà´ªà´±à´¯àµà´¨àµà´¨ à´²à´•àµà´šà´°àµâ€ à´‰à´³àµà´³à´Ÿà´•àµà´•à´¤àµà´¤àµ† à´…à´Ÿà´¿à´¸àµà´¥à´¾à´¨à´®à´¾à´•àµà´•à´¿ à´‰à´¤àµà´¤à´°à´‚ à´¨à´²àµâ€à´•àµà´•:\n\n"
            f"{context}\n\n"
            f"à´šàµ‹à´¦àµà´¯à´‚: {question}"
        )
        system = _SYSTEM_ML
    else:
        user_message = (
            f"Use the following course context to answer the question directly:\n\n"
            f"{context}\n\n"
            f"Question: {question}"
        )
        system = _SYSTEM_EN

    client = _get_groq_client()
    response = client.chat.completions.create(
        model=_GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
        max_tokens=max(128, int(getattr(settings, "CHATBOT_MAX_TOKENS", 384))),
    )
    return _clean_answer_style(response.choices[0].message.content.strip())
