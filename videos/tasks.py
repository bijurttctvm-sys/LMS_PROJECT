import io
import json
import logging
import os
import re
from collections import Counter

from celery import shared_task

logger = logging.getLogger(__name__)

WORDS_PER_CHUNK = 500
TOPIC_SIM_THRESHOLD = 0.4


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

    Video.objects.filter(id=video_id).update(status=Video.Status.PROCESSING)

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

        generate_pdfs.delay(video_id)

    except Exception as exc:
        logger.exception('[%s] process_study_material failed: %s', video_id, exc)
        Video.objects.filter(id=video_id).update(status=Video.Status.FAILED)
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# generate_pdfs — Phase 4 (reportlab → R2)
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3)
def generate_pdfs(self, video_id):
    from videos.models import Video
    from utils.r2_storage import upload_file

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        logger.error('[%s] generate_pdfs: video not found', video_id)
        return

    try:
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
            status=Video.Status.READY,
        )
        logger.info('[%s] PDFs uploaded and status set to READY', video_id)

        generate_embeddings.delay(video_id)

    except Exception as exc:
        logger.exception('[%s] generate_pdfs failed: %s', video_id, exc)
        Video.objects.filter(id=video_id).update(status=Video.Status.FAILED)
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
        raw_texts = [c.text for c in pending]

        embeddings = _get_embeddings(video_id, raw_texts)

        vector_ids = upsert_chunks(
            video_id, video.course_id, pending, embeddings, video.language_code
        )

        for chunk, vid_id in zip(pending, vector_ids):
            TranscriptChunk.objects.filter(id=chunk.id).update(embedding_id=vid_id)

        logger.info('[%s] Upserted %d vectors to Pinecone', video_id, len(vector_ids))

        # Auto-generate quiz draft if none exist yet for this video
        _auto_trigger_quiz(video_id)

    except Exception as exc:
        logger.exception('[%s] generate_embeddings failed: %s', video_id, exc)
        raise self.retry(exc=exc, countdown=120)


def _auto_trigger_quiz(video_id):
    """Queue quiz generation only if no quiz or pending drafts exist for this video."""
    try:
        from quizzes.models import Quiz, QuizDraft
        already_has_quiz   = Quiz.objects.filter(video_id=video_id).exists()
        already_has_drafts = QuizDraft.objects.filter(video_id=video_id).exists()
        if already_has_quiz or already_has_drafts:
            logger.info('[%s] Skipping auto quiz — quiz/drafts already exist', video_id)
            return
        generate_quiz.delay(video_id)
        logger.info('[%s] Auto-queued quiz generation', video_id)
    except Exception as exc:
        logger.warning('[%s] Auto quiz trigger failed: %s', video_id, exc)


def _get_embeddings(video_id, raw_texts):
    """
    Try Modal GPU first; if unavailable, fall back to local CPU embedding.
    Both produce identical 1024-dim multilingual-e5-large vectors.
    """
    # Modal GPU path — fast, preferred for large batches
    try:
        from modal_functions.transcribe import EmbeddingGenerator
        embedder = EmbeddingGenerator()
        # Modal expects "passage: " prefixed texts
        prefixed = [f"passage: {t}" for t in raw_texts]
        embeddings = embedder.generate.remote(prefixed)
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
    import json as _json
    import re as _re
    from django.conf import settings as _settings
    from django.contrib.auth import get_user_model
    from django.core.mail import send_mail
    from videos.models import Video
    from quizzes.models import QuizDraft

    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        logger.error('[%s] generate_quiz: video not found', video_id)
        return

    transcript = (video.english_transcript or '').strip()
    if not transcript:
        logger.warning('[%s] generate_quiz: no English transcript available', video_id)
        return

    excerpt = transcript[:4000]

    prompt = (
        "You are a quiz generator for an online course. "
        "Generate exactly 10 multiple-choice questions from the lecture transcript below.\n\n"
        "Rules:\n"
        "- Each question must test conceptual understanding, not just literal recall.\n"
        "- Each question must have exactly 4 options labelled A, B, C, D.\n"
        "- Exactly one option is correct.\n"
        "- Include a brief explanation (1-2 sentences) for the correct answer.\n\n"
        "Return ONLY a valid JSON array of 10 objects. "
        "Each object must have exactly these keys:\n"
        '  "question", "option_a", "option_b", "option_c", "option_d", '
        '"correct_option" (value: "a"/"b"/"c"/"d"), "explanation"\n\n'
        f"Transcript:\n{excerpt}"
    )

    try:
        from groq import Groq
        client = Groq(api_key=_settings.GROQ_API_KEY)
        resp = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            messages=[
                {'role': 'system', 'content': 'You are a helpful quiz generator. Return only valid JSON, no markdown.'},
                {'role': 'user',   'content': prompt},
            ],
            temperature=0.4,
            max_tokens=3000,
        )
        raw = resp.choices[0].message.content.strip()

        fence = _re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
        if fence:
            raw = fence.group(1)

        questions = _json.loads(raw)
        if not isinstance(questions, list):
            raise ValueError(f'Expected JSON array, got {type(questions).__name__}')

        drafts = []
        for q in questions[:10]:
            correct = str(q.get('correct_option', 'a')).strip().lower()
            if correct not in ('a', 'b', 'c', 'd'):
                correct = 'a'
            drafts.append(QuizDraft(
                video          = video,
                question_text  = str(q.get('question', '')).strip(),
                option_a       = str(q.get('option_a', '')).strip(),
                option_b       = str(q.get('option_b', '')).strip(),
                option_c       = str(q.get('option_c', '')).strip(),
                option_d       = str(q.get('option_d', '')).strip(),
                correct_option = correct,
                explanation    = str(q.get('explanation', '')).strip(),
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
        if admin_emails:
            subject = f'Quiz draft ready for review: {video.title}'
            body = (
                f"Hello,\n\n"
                f"A new quiz draft ({len(drafts)} questions) has been generated "
                f"for the video:\n\n"
                f"  Title  : {video.title}\n"
                f"  Course : {video.course.title}\n\n"
                f"Please log in to the LMS admin to review and approve the questions "
                f"before publishing.\n\n"
                f"— LMS Automated System"
            )
            send_mail(subject, body, _settings.DEFAULT_FROM_EMAIL, admin_emails, fail_silently=True)
            logger.info('[%s] Notified %d admin(s) of quiz draft', video_id, len(admin_emails))

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

    body = text or 'No transcript available.'
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
