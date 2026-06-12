import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_GROQ_MODEL = "llama-3.1-8b-instant"

_SYSTEM_EN = (
    "You are a helpful teaching assistant for an online lecture course. "
    "Answer questions clearly and concisely based only on the provided lecture content. "
    "If the answer is not in the content, say so honestly."
)

_SYSTEM_ML = (
    "നിങ്ങള്‍ ഒരു ഓണ്‍ലൈന്‍ ലക്ചര്‍ കോഴ്‌സിനുള്ള സഹായകരമായ അധ്യാപന സഹായകനാണ്. "
    "നല്‍കിയ ലക്ചര്‍ ഉള്ളടക്കത്തെ അടിസ്ഥാനമാക്കി മാത്രം ചോദ്യങ്ങള്‍ക്ക് ഉത്തരം നല്‍കുക."
)


def get_answer(question: str, context_chunks: list, language: str = "en") -> str:
    """
    Build a RAG prompt from retrieved chunks and call Groq.

    context_chunks : list of dicts with at least a 'text' key
    language       : 'en' or 'ml'
    Returns the answer string.
    """
    from groq import Groq

    if not context_chunks:
        return (
            "I could not find relevant information in the course material to answer that question."
            if language == "en"
            else "ഈ ചോദ്യത്തിന് ഉത്തരം നല്‍കാന്‍ കോഴ്‌സ് മെറ്റീരിയലില്‍ നിന്ന് പ്രസക്തമായ വിവരങ്ങള്‍ ലഭ്യമല്ല."
        )

    context = "\n\n".join(
        f"[Chunk {i + 1}] {c['text']}" for i, c in enumerate(context_chunks)
    )

    if language == "ml":
        user_message = (
            f"ഇനിപ്പറയുന്ന ലക്ചര്‍ ഉള്ളടക്കത്തെ അടിസ്ഥാനമാക്കി ഉത്തരം നല്‍കുക:\n\n"
            f"{context}\n\n"
            f"ചോദ്യം: {question}"
        )
        system = _SYSTEM_ML
    else:
        user_message = (
            f"Answer based on this lecture content:\n\n"
            f"{context}\n\n"
            f"Question: {question}"
        )
        system = _SYSTEM_EN

    client = Groq(api_key=settings.GROQ_API_KEY)
    response = client.chat.completions.create(
        model=_GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()
