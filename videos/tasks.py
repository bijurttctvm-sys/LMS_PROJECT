import io
import json
import logging
import os
import re
from collections import Counter
from difflib import SequenceMatcher

from celery import shared_task
from django.utils import timezone
from quizzes.constants import QUIZ_TARGET_QUESTION_COUNT

logger = logging.getLogger(__name__)

WORDS_PER_CHUNK = 500
TOPIC_SIM_THRESHOLD = 0.4
_QUIZ_GROQ_MODEL = 'llama-3.1-8b-instant'
_QUIZ_REQUIRED_KEYS = (
    'question',
    'option_a',
    'option_b',
    'option_c',
    'option_d',
    'correct_option',
    'explanation',
)
_QUIZ_JSON_FENCE_RE = re.compile(r'```(?:json)?\s*([\s\S]+?)\s*```', re.IGNORECASE)
_SMART_QUOTES_TRANS = str.maketrans({
    '\u2018': "'",
    '\u2019': "'",
    '\u201c': '"',
    '\u201d': '"',
})
_QUIZ_DUPLICATE_SIMILARITY_THRESHOLD = 0.9


def _missing_required_settings(*names):
    from django.conf import settings as _settings

    return [name for name in names if not getattr(_settings, name, '')]


# ---------------------------------------------------------------------------
# process_study_material — chunks the uploaded content then kicks off
#                          PDF generation and embedding pipeline
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3)
def process_study_material(self, video_id):
    from videos.models import Video, TranscriptChunk

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        logger.error('[%s] process_study_material: video not found', video_id)
        return

    Video.objects.filter(id=video_id).update(
        status=Video.Status.PROCESSING,
        processing_started_at=timezone.now(),
    )

    try:
        english_text = (video.english_transcript or '').strip()
        if not english_text:
            raise ValueError('No study material content to process')

        # Remove stale Pinecone vectors before re-indexing
        try:
            from utils.pinecone_client import delete_video_chunks
            delete_video_chunks(video_id)
            logger.info('[%s] Deleted old Pinecone vectors', video_id)
        except Exception as pc_exc:
            logger.warning('[%s] Could not delete old Pinecone vectors: %s', video_id, pc_exc)

        # Remove existing DB chunks so re-uploads start clean
        TranscriptChunk.objects.filter(video_id=video_id).delete()

        chunk_texts = _split_text_to_chunks(english_text, WORDS_PER_CHUNK)
        logger.info('[%s] Split material into %d chunks', video_id, len(chunk_texts))

        chunk_objects = [
            TranscriptChunk(
                video_id=video_id,
                chunk_index=idx,
                text=text,
                # Approximate section markers: 60 s per chunk
                start_time=float(idx * 60),
                end_time=float((idx + 1) * 60),
            )
            for idx, text in enumerate(chunk_texts)
        ]
        TranscriptChunk.objects.bulk_create(chunk_objects)

        all_chunks = list(
            TranscriptChunk.objects.filter(video_id=video_id).order_by('chunk_index')
        )
        _mark_topic_segments(all_chunks)

        # Quiz drafts depend on the saved study material, not on PDFs/vectors.
        # Queue them as early as possible so review can start immediately.
        queue_quiz_generation(video_id)
        generate_pdfs.delay(video_id)

    except Exception as exc:
        logger.exception('[%s] process_study_material failed: %s', video_id, exc)
        Video.objects.filter(id=video_id).update(
            status=Video.Status.FAILED,
            processing_started_at=None,
        )
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# generate_pdfs — Phase 4 (reportlab → R2)
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3)
def generate_pdfs(self, video_id):
    from videos.models import Video
    from utils.r2_storage import upload_file, uses_object_storage

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        logger.error('[%s] generate_pdfs: video not found', video_id)
        return

    try:
        # Render disks are service-local. In worker mode, skip PDF export
        # uploads unless shared object storage is configured.
        if not uses_object_storage():
            Video.objects.filter(id=video_id).update(
                english_pdf_key='',
                malayalam_pdf_key='',
                status=Video.Status.PROCESSING,
            )
            logger.info(
                '[%s] Shared object storage not configured; skipping PDF export upload',
                video_id,
            )
            generate_embeddings.delay(video_id)
            return

        en_pdf = _build_pdf(
            title=f'{video.title} — English Transcript',
            text=video.english_transcript,
            language='en',
        )
        ml_pdf = _build_pdf(
            title=f'{video.title} — Malayalam Transcript',
            text=video.malayalam_transcript,
            language='ml',
        )

        if video.video_key:
            base = video.video_key.rsplit('.', 1)[0].replace('videos/', 'pdfs/', 1)
        else:
            base = f'pdfs/{video.course_id}/{video.id}'

        en_key = f'{base}_en.pdf'
        ml_key = f'{base}_ml.pdf'

        upload_file(io.BytesIO(en_pdf), en_key)
        upload_file(io.BytesIO(ml_pdf), ml_key)

        Video.objects.filter(id=video_id).update(
            english_pdf_key=en_key,
            malayalam_pdf_key=ml_key,
            status=Video.Status.PROCESSING,
        )
        logger.info('[%s] PDFs uploaded; waiting for embeddings before marking READY', video_id)

        generate_embeddings.delay(video_id)

    except Exception as exc:
        logger.exception('[%s] generate_pdfs failed: %s', video_id, exc)
        Video.objects.filter(id=video_id).update(
            status=Video.Status.FAILED,
            processing_started_at=None,
        )
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# generate_embeddings — Phase 5 (e5-large → Pinecone)
#   Primary:  Modal GPU  (fast, batch)
#   Fallback: CPU via utils.embeddings  (always available)
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3)
def generate_embeddings(self, video_id):
    from videos.models import Video, TranscriptChunk
    from utils.pinecone_client import upsert_chunks

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        logger.error('[%s] generate_embeddings: video not found', video_id)
        return

    chunks = list(
        TranscriptChunk.objects.filter(video_id=video_id).order_by('chunk_index')
    )
    if not chunks:
        logger.info('[%s] No chunks to embed', video_id)
        return

    pending = [c for c in chunks if not c.embedding_id]
    if not pending:
        logger.info('[%s] All chunks already embedded', video_id)
        return

    try:
        missing_settings = _missing_required_settings(
            'PINECONE_API_KEY',
            'PINECONE_INDEX_NAME',
        )
        if missing_settings:
            Video.objects.filter(id=video_id).update(
                status=Video.Status.FAILED,
                processing_started_at=None,
            )
            logger.error(
                '[%s] generate_embeddings aborted because required settings are missing: %s',
                video_id,
                ', '.join(missing_settings),
            )
            return
        raw_texts = [c.text for c in pending]

        embeddings = _get_embeddings(video_id, raw_texts)

        vector_ids = upsert_chunks(
            video_id, video.course_id, pending, embeddings, video.language_code
        )

        for chunk, vid_id in zip(pending, vector_ids):
            TranscriptChunk.objects.filter(id=chunk.id).update(embedding_id=vid_id)

        Video.objects.filter(id=video_id).update(
            status=Video.Status.READY,
            processing_started_at=None,
        )
        logger.info('[%s] Upserted %d vectors to Pinecone', video_id, len(vector_ids))

    except Exception as exc:
        logger.exception('[%s] generate_embeddings failed: %s', video_id, exc)
        raise self.retry(exc=exc, countdown=120)


def queue_quiz_generation(video_id, force=False):
    """
    Queue quiz generation unless the video already has a quiz or reviewable drafts.

    Returns:
        True when quiz generation was queued.
        False when queueing was intentionally skipped because reviewable drafts
        or a quiz already exist.
        None when queueing failed unexpectedly.
    """
    try:
        missing_settings = _missing_required_settings('GROQ_API_KEY')
        if missing_settings:
            logger.warning(
                '[%s] Quiz queue skipped because required settings are missing: %s',
                video_id,
                ', '.join(missing_settings),
            )
            return None
        from quizzes.models import Quiz, QuizDraft
        already_has_quiz = Quiz.objects.filter(video_id=video_id).exists()
        already_has_drafts = QuizDraft.objects.filter(
            video_id=video_id,
            status__in=[QuizDraft.Status.PENDING, QuizDraft.Status.APPROVED],
        ).exists()
        if not force and (already_has_quiz or already_has_drafts):
            logger.info('[%s] Skipping quiz queue because quiz/drafts already exist', video_id)
            return False
        generate_quiz.delay(video_id)
        logger.info('[%s] Queued quiz generation', video_id)
        return True
    except Exception as exc:
        logger.warning('[%s] Quiz queue failed: %s', video_id, exc)
        return None


def _auto_trigger_quiz(video_id):
    """Backward-compatible wrapper for older call sites."""
    return queue_quiz_generation(video_id)


def _build_quiz_prompt(excerpt, repair=False):
    prefix = (
        "A previous attempt returned invalid or truncated JSON. "
        "Regenerate the quiz from scratch.\n\n"
        if repair else
        ""
    )
    return (
        f"{prefix}"
        "You are a quiz generator for an online course. "
        f"Generate exactly {QUIZ_TARGET_QUESTION_COUNT} multiple-choice questions from the lecture transcript below.\n\n"
        "Rules:\n"
        "- Each question must test conceptual understanding, not just literal recall.\n"
        "- Each question must have exactly 4 options labelled A, B, C, D.\n"
        "- Exactly one option is correct.\n"
        "- Include a brief explanation (1-2 short sentences) for the correct answer.\n"
        "- Keep each option concise.\n"
        "- Escape any double quotes inside values.\n\n"
        "Return ONLY a valid compact JSON array on a single line. "
        "Do not use markdown, comments, or extra text.\n"
        "Each object must have exactly these keys:\n"
        '  "question", "option_a", "option_b", "option_c", "option_d", '
        '"correct_option" (value: "a"/"b"/"c"/"d"), "explanation"\n\n'
        f"Transcript:\n{excerpt}"
    )


def _request_quiz_response(client, excerpt, repair=False):
    response = client.chat.completions.create(
        model=_QUIZ_GROQ_MODEL,
        messages=[
            {
                'role': 'system',
                'content': (
                    'You are a helpful quiz generator. '
                    'Return only compact valid JSON with no markdown.'
                ),
            },
            {'role': 'user', 'content': _build_quiz_prompt(excerpt, repair=repair)},
        ],
        temperature=0.2 if repair else 0.3,
        max_tokens=4096,
    )
    return (response.choices[0].message.content or '').strip()


def _normalise_quiz_questions(questions, expected_count=QUIZ_TARGET_QUESTION_COUNT):
    if isinstance(questions, dict):
        questions = [questions]
    if not isinstance(questions, list):
        raise ValueError(f'Expected JSON array, got {type(questions).__name__}')
    if len(questions) < expected_count:
        raise ValueError(f'Expected {expected_count} quiz questions, got {len(questions)}')

    cleaned = []
    for idx, item in enumerate(questions[:expected_count], start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Question {idx} must be an object')

        payload = {key: str(item.get(key, '')).strip() for key in _QUIZ_REQUIRED_KEYS}
        if not payload['question']:
            raise ValueError(f'Question {idx} is missing question text')
        for option_key in ('option_a', 'option_b', 'option_c', 'option_d'):
            if not payload[option_key]:
                raise ValueError(f'Question {idx} is missing {option_key}')

        correct = payload['correct_option'].lower()
        if correct not in ('a', 'b', 'c', 'd'):
            raise ValueError(f'Question {idx} has invalid correct_option: {correct!r}')
        payload['correct_option'] = correct
        cleaned.append(payload)

    return cleaned


def _parse_quiz_response(raw, expected_count=QUIZ_TARGET_QUESTION_COUNT):
    raw = (raw or '').strip()
    if not raw:
        raise ValueError('Empty quiz response')

    normalised_raw = raw.translate(_SMART_QUOTES_TRANS)
    candidates = []

    fence = _QUIZ_JSON_FENCE_RE.search(normalised_raw)
    if fence:
        candidates.append(fence.group(1).strip())

    candidates.append(normalised_raw)

    first_bracket = normalised_raw.find('[')
    last_bracket = normalised_raw.rfind(']')
    if 0 <= first_bracket < last_bracket:
        candidates.append(normalised_raw[first_bracket:last_bracket + 1].strip())

    decoder = json.JSONDecoder()
    seen = set()
    last_error = None

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            try:
                parsed, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError as raw_exc:
                last_error = raw_exc
                continue

        return _normalise_quiz_questions(parsed, expected_count=expected_count)

    if last_error:
        raise last_error
    raise ValueError('Could not parse quiz response')


def _normalise_question_text(text):
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def _question_is_duplicate(candidate_text, existing_texts):
    candidate = _normalise_question_text(candidate_text)
    if not candidate:
        return True

    for existing_text in existing_texts:
        existing = _normalise_question_text(existing_text)
        if not existing:
            continue
        if candidate == existing:
            return True
        if SequenceMatcher(None, candidate, existing).ratio() >= _QUIZ_DUPLICATE_SIMILARITY_THRESHOLD:
            return True
    return False


def _existing_question_texts(video):
    from quizzes.models import QuizDraft, QuizQuestion

    draft_texts = QuizDraft.objects.filter(video=video).values_list('question_text', flat=True)
    published_texts = QuizQuestion.objects.filter(quiz__video=video).values_list('question_text', flat=True)
    return [text.strip() for text in list(draft_texts) + list(published_texts) if (text or '').strip()]


def _format_question_list_for_prompt(question_texts):
    unique = []
    seen = set()
    for text in question_texts:
        cleaned = ' '.join((text or '').split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned[:240])
    if not unique:
        return 'None.'
    return '\n'.join(f'- {text}' for text in unique[:25])


def _build_replacement_quiz_prompt(excerpt, existing_questions, rejected_question='', rejection_note='', repair=False):
    prefix = (
        "The previous replacement was invalid, duplicate, or truncated. "
        "Generate a different replacement question from scratch.\n\n"
        if repair else
        ""
    )
    question_request = (
        "Generate exactly 1 replacement multiple-choice question from the lecture transcript below.\n\n"
        if rejected_question else
        "Generate exactly 1 additional multiple-choice question from the lecture transcript below.\n\n"
    )
    uniqueness_rule = (
        "- The replacement must test a different concept or angle from the rejected question.\n"
        if rejected_question else
        "- The new question must cover a different concept or angle from the existing questions listed below.\n"
    )
    rejected_context = ''
    if rejected_question:
        rejected_context += f"Rejected question:\n{rejected_question.strip()[:300]}\n\n"
    if rejection_note:
        rejected_context += f"Reviewer feedback:\n{rejection_note.strip()[:300]}\n\n"

    return (
        f"{prefix}"
        "You are a quiz generator for an online course. "
        f"{question_request}"
        "Rules:\n"
        f"{uniqueness_rule}"
        "- It must not duplicate or closely paraphrase any existing question listed below.\n"
        "- The question must test conceptual understanding, not just literal recall.\n"
        "- Include exactly 4 options labelled A, B, C, D.\n"
        "- Exactly one option is correct.\n"
        "- Include a brief explanation (1-2 short sentences) for the correct answer.\n"
        "- Keep the question and options concise.\n"
        "- Escape any double quotes inside values.\n\n"
        "Existing questions to avoid:\n"
        f"{_format_question_list_for_prompt(existing_questions)}\n\n"
        f"{rejected_context}"
        "Return ONLY a valid compact JSON array with 1 object on a single line. "
        "Do not use markdown, comments, or extra text.\n"
        "The object must have exactly these keys:\n"
        '  "question", "option_a", "option_b", "option_c", "option_d", '
        '"correct_option" (value: "a"/"b"/"c"/"d"), "explanation"\n\n'
        f"Transcript:\n{excerpt}"
    )


def _request_replacement_quiz_response(client, excerpt, existing_questions, rejected_question='', rejection_note='', repair=False):
    response = client.chat.completions.create(
        model=_QUIZ_GROQ_MODEL,
        messages=[
            {
                'role': 'system',
                'content': (
                    'You are a helpful quiz generator. '
                    'Return only compact valid JSON with no markdown.'
                ),
            },
            {
                'role': 'user',
                'content': _build_replacement_quiz_prompt(
                    excerpt,
                    existing_questions,
                    rejected_question=rejected_question,
                    rejection_note=rejection_note,
                    repair=repair,
                ),
            },
        ],
        temperature=0.2,
        max_tokens=1024,
    )
    return (response.choices[0].message.content or '').strip()


def generate_replacement_quiz_draft(video_id, rejected_question='', rejection_note='', max_attempts=3):
    from django.conf import settings as _settings
    from groq import Groq
    from quizzes.models import QuizDraft
    from videos.models import Video

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist as exc:
        raise ValueError(f'Video {video_id} not found') from exc

    transcript = (video.english_transcript or '').strip()
    if not transcript:
        raise ValueError('Quiz replacement requires English study material')

    excerpt = transcript[:4000]
    existing_questions = _existing_question_texts(video)
    if rejected_question and rejected_question.strip():
        existing_questions.append(rejected_question.strip())

    client = Groq(api_key=_settings.GROQ_API_KEY)
    last_exc = None

    for attempt in range(max_attempts):
        raw = _request_replacement_quiz_response(
            client,
            excerpt,
            existing_questions,
            rejected_question=rejected_question,
            rejection_note=rejection_note,
            repair=attempt > 0,
        )
        try:
            questions = _parse_quiz_response(raw, expected_count=1)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                '[%s] Replacement quiz parse failed on attempt %d/%d: %s',
                video_id,
                attempt + 1,
                max_attempts,
                exc,
            )
            continue

        candidate = questions[0]
        if _question_is_duplicate(candidate['question'], existing_questions):
            last_exc = ValueError('Replacement quiz question duplicated an existing question')
            existing_questions.append(candidate['question'])
            logger.warning(
                '[%s] Replacement quiz candidate duplicated an existing question on attempt %d/%d',
                video_id,
                attempt + 1,
                max_attempts,
            )
            continue

        return QuizDraft.objects.create(
            video=video,
            question_text=candidate['question'],
            option_a=candidate['option_a'],
            option_b=candidate['option_b'],
            option_c=candidate['option_c'],
            option_d=candidate['option_d'],
            correct_option=candidate['correct_option'],
            explanation=candidate['explanation'],
            status=QuizDraft.Status.PENDING,
        )

    if last_exc:
        raise last_exc
    raise ValueError('Could not generate a replacement quiz question')


def generate_replacement_quiz_drafts(video_id, rejected_questions, rejection_note=''):
    """Generate one replacement draft for each rejected question."""
    replacements = []
    cleaned_questions = [
        question.strip()
        for question in (rejected_questions or [])
        if (question or '').strip()
    ]

    for index, rejected_question in enumerate(cleaned_questions, start=1):
        try:
            replacements.append(
                generate_replacement_quiz_draft(
                    video_id,
                    rejected_question=rejected_question,
                    rejection_note=rejection_note,
                )
            )
        except Exception as exc:
            logger.warning(
                '[%s] Replacement quiz generation stopped after %d/%d questions: %s',
                video_id,
                len(replacements),
                len(cleaned_questions),
                exc,
            )
            if not replacements and index == 1:
                raise
            break

    return replacements


def generate_missing_quiz_drafts(video_id, missing_count, note=''):
    """Generate additional draft questions to reach the target quiz count."""
    missing_count = int(missing_count or 0)
    if missing_count <= 0:
        return []

    replacements = []
    guidance = note or 'Generate additional unique quiz questions to complete the quiz set.'

    for attempt in range(missing_count):
        try:
            replacements.append(
                generate_replacement_quiz_draft(
                    video_id,
                    rejected_question='',
                    rejection_note=guidance,
                )
            )
        except Exception as exc:
            logger.warning(
                '[%s] Missing quiz question generation stopped after %d/%d questions: %s',
                video_id,
                len(replacements),
                missing_count,
                exc,
            )
            if not replacements and attempt == 0:
                raise
            break

    return replacements


def _get_embeddings(video_id, raw_texts):
    """
    Try Modal GPU first; if unavailable, fall back to local CPU embedding.
    Both produce identical 1024-dim multilingual-e5-large vectors.
    """
    # Modal GPU path — fast, preferred for large batches
    try:
        from utils.embeddings import generate_remote_passage_embeddings
        embeddings = generate_remote_passage_embeddings(raw_texts)
        logger.info('[%s] Embeddings generated via Modal GPU (%d chunks)', video_id, len(raw_texts))
        return embeddings
    except Exception as modal_exc:
        logger.warning(
            '[%s] Modal GPU unavailable (%s) — falling back to CPU embedding',
            video_id, modal_exc,
        )

    # CPU fallback — always works, slower for large batches
    from utils.embeddings import generate_passage_embeddings
    embeddings = generate_passage_embeddings(raw_texts)
    logger.info('[%s] Embeddings generated via CPU fallback (%d chunks)', video_id, len(raw_texts))
    return embeddings


# ---------------------------------------------------------------------------
# generate_quiz — Phase 6 (Groq MCQ generation → QuizDraft)
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2)
def generate_quiz(self, video_id):
    from django.conf import settings as _settings
    from django.contrib.auth import get_user_model
    from django.core.mail import send_mail
    from videos.models import Video
    from quizzes.models import Quiz, QuizDraft

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        logger.error('[%s] generate_quiz: video not found', video_id)
        return

    transcript = (video.english_transcript or '').strip()
    if not transcript:
        logger.warning('[%s] generate_quiz: no English transcript available', video_id)
        return

    existing_drafts = QuizDraft.objects.filter(
        video=video,
        status__in=[QuizDraft.Status.PENDING, QuizDraft.Status.APPROVED],
    ).exists()
    if Quiz.objects.filter(video=video).exists() or existing_drafts:
        logger.info('[%s] generate_quiz: skipping because quiz/drafts already exist', video_id)
        return

    excerpt = transcript[:4000]

    try:
        missing_settings = _missing_required_settings('GROQ_API_KEY')
        if missing_settings:
            logger.error(
                '[%s] generate_quiz aborted because required settings are missing: %s',
                video_id,
                ', '.join(missing_settings),
            )
            return
        from groq import Groq
        client = Groq(api_key=_settings.GROQ_API_KEY)
        raw = _request_quiz_response(client, excerpt, repair=False)
        try:
            questions = _parse_quiz_response(raw)
        except Exception as parse_exc:
            logger.warning(
                '[%s] Primary quiz JSON parse failed: %s. Retrying with stricter prompt.',
                video_id,
                parse_exc,
            )
            raw = _request_quiz_response(client, excerpt, repair=True)
            questions = _parse_quiz_response(raw)

        drafts = []
        for q in questions:
            drafts.append(QuizDraft(
                video          = video,
                question_text  = q['question'],
                option_a       = q['option_a'],
                option_b       = q['option_b'],
                option_c       = q['option_c'],
                option_d       = q['option_d'],
                correct_option = q['correct_option'],
                explanation    = q['explanation'],
                status         = QuizDraft.Status.PENDING,
            ))
        QuizDraft.objects.bulk_create(drafts)
        logger.info('[%s] Created %d quiz draft questions', video_id, len(drafts))

        User = get_user_model()
        admin_emails = list(
            User.objects.filter(role='admin')
            .exclude(email='')
            .values_list('email', flat=True)
        )
        recipients = set(admin_emails)
        instructor = getattr(video.course, 'instructor', None)
        if instructor and instructor.email:
            recipients.add(instructor.email)
        if recipients:
            subject = f'Quiz draft ready for review: {video.title}'
            body = (
                f"Hello,\n\n"
                f"A new quiz draft ({len(drafts)} questions) has been generated "
                f"for the video:\n\n"
                f"  Title  : {video.title}\n"
                f"  Course : {video.course.title}\n\n"
                f"Please log in to the LMS quiz draft review screen to review and approve the questions "
                f"before publishing.\n\n"
                f"— LMS Automated System"
            )
            send_mail(
                subject,
                body,
                _settings.DEFAULT_FROM_EMAIL,
                sorted(recipients),
                fail_silently=True,
            )
            logger.info('[%s] Notified %d reviewer(s) of quiz draft', video_id, len(recipients))

    except Exception as exc:
        logger.exception('[%s] generate_quiz failed: %s', video_id, exc)
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Helpers — text chunking
# ---------------------------------------------------------------------------

def _split_text_to_chunks(text, words_per_chunk=500):
    """Split text into chunks of approximately words_per_chunk words, respecting paragraph boundaries."""
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks, current, current_count = [], [], 0
    for para in paragraphs:
        para_words = len(para.split())
        if current_count + para_words > words_per_chunk and current:
            chunks.append('\n\n'.join(current))
            current, current_count = [para], para_words
        else:
            current.append(para)
            current_count += para_words

    if current:
        chunks.append('\n\n'.join(current))

    return chunks or [text]


# ---------------------------------------------------------------------------
# Helpers — topic segmentation
# ---------------------------------------------------------------------------

def _tfidf_vector(text):
    words = re.findall(r'\w+', (text or '').lower())
    return Counter(words)


def _cosine_sim(v1, v2):
    if not v1 or not v2:
        return 0.0
    common = set(v1) & set(v2)
    dot = sum(v1[w] * v2[w] for w in common)
    mag = (sum(c * c for c in v1.values()) ** 0.5) * (sum(c * c for c in v2.values()) ** 0.5)
    return dot / mag if mag else 0.0


def _mark_topic_segments(chunks):
    if len(chunks) < 2:
        return
    vectors = [_tfidf_vector(c.text) for c in chunks]
    seg_idx = 0
    updates = []
    for i, chunk in enumerate(chunks):
        if i > 0 and _cosine_sim(vectors[i - 1], vectors[i]) < TOPIC_SIM_THRESHOLD:
            seg_idx += 1
        updates.append((chunk.id, seg_idx))
    from videos.models import TranscriptChunk
    for chunk_id, seg in updates:
        TranscriptChunk.objects.filter(id=chunk_id).update(topic_segment=seg)


# ---------------------------------------------------------------------------
# Helpers — PDF generation
# ---------------------------------------------------------------------------

def _build_pdf(title, text, language='en'):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    font_name = 'Helvetica' if language == 'en' else _register_unicode_font()

    title_style = ParagraphStyle(
        'LMSTitle', parent=styles['Title'],
        fontName=font_name, fontSize=16, spaceAfter=16,
    )
    body_style = ParagraphStyle(
        'LMSBody', parent=styles['Normal'],
        fontName=font_name, fontSize=11, leading=17, spaceAfter=8,
    )

    story = [Paragraph(_xml_safe(title), title_style), Spacer(1, 0.4 * cm)]

    body = text or 'No study material available.'
    for para in re.split(r'\n{2,}', body):
        para = para.strip()
        if para:
            story.append(Paragraph(_xml_safe(para), body_style))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def _xml_safe(text):
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def _register_unicode_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        '/usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf',
        '/usr/share/fonts/noto/NotoSansMalayalam-Regular.ttf',
        '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'C:/Windows/Fonts/arial.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('UniFont', path))
                return 'UniFont'
            except Exception:
                continue
    return 'Helvetica'
