import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from users.models import User
from .forms import StudyMaterialUploadForm, VideoUploadForm
from .models import Video


def _instructor_or_admin(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.role not in (User.Role.ADMIN, User.Role.INSTRUCTOR):
            messages.error(request, 'Instructors and admins only.')
            return redirect('login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def _try_signed_url(key):
    if not key:
        return None
    try:
        from utils.r2_storage import get_signed_url
        return get_signed_url(key)
    except Exception:
        return None


def _course_is_locked(course, user):
    """Return True when an instructor course already has 1 video + 1 study material."""
    if user.role == User.Role.ADMIN:
        return False
    video_qs = Video.objects.filter(course=course)
    if not video_qs.exists():
        return False
    return bool(video_qs.first().english_transcript)


@_instructor_or_admin
def upload_video_view(request):
    if request.method == 'POST':
        form = VideoUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            course = form.cleaned_data['course']

            # Instructors: max 1 video per course
            if request.user.role == User.Role.INSTRUCTOR:
                if Video.objects.filter(course=course).exists():
                    messages.error(
                        request,
                        'A video already exists for this course. '
                        'Each course can have only one video.'
                    )
                    return render(request, 'videos/upload.html', {'form': form})

            video = form.save(commit=False)
            video_file = request.FILES.get('video_file')

            if video_file:
                ext = (
                    video_file.name.rsplit('.', 1)[-1].lower()
                    if '.' in video_file.name else 'mp4'
                )
                key = f'videos/{course.id}/{uuid.uuid4()}.{ext}'
                try:
                    from utils.r2_storage import upload_file
                    upload_file(video_file, key)
                except Exception as exc:
                    messages.error(request, f'Upload to R2 failed: {exc}')
                    return render(request, 'videos/upload.html', {'form': form})
                video.video_key = key

            video.status = Video.Status.UPLOADED
            video.save()

            messages.success(
                request,
                f'"{video.title}" saved. Now upload the study material to make it searchable.'
            )
            return redirect('upload-material', video_id=video.id)
    else:
        form = VideoUploadForm(user=request.user)
    return render(request, 'videos/upload.html', {'form': form})


@_instructor_or_admin
def upload_material_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)

    # Instructors: block re-upload once course is locked
    if request.user.role == User.Role.INSTRUCTOR:
        if _course_is_locked(video.course, request.user):
            messages.error(
                request,
                'This course already has a video and study material. '
                'Content cannot be modified once both are uploaded.'
            )
            return redirect('video-detail', video_id=video.id)

    if request.method == 'POST':
        form = StudyMaterialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            material_file = request.FILES.get('material_file')
            english_text  = form.cleaned_data.get('english_content', '').strip()
            malayalam_text = form.cleaned_data.get('malayalam_content', '').strip()

            if material_file:
                try:
                    english_text = _extract_text(material_file)
                except Exception as exc:
                    messages.error(request, f'Could not extract text from file: {exc}')
                    return render(request, 'videos/upload_material.html', {
                        'form': form, 'video': video,
                    })

            video.english_transcript = english_text
            if malayalam_text:
                video.malayalam_transcript = malayalam_text
            video.save(update_fields=['english_transcript', 'malayalam_transcript'])

            try:
                from videos.tasks import process_study_material
                process_study_material.delay(video.id)
                messages.success(
                    request,
                    'Study material saved. Generating PDF and search index in the background...'
                )
            except Exception as exc:
                messages.warning(
                    request,
                    f'Material saved but background processing unavailable: {exc}'
                )

            return redirect('video-detail', video_id=video.id)
    else:
        form = StudyMaterialUploadForm(initial={
            'english_content':   video.english_transcript,
            'malayalam_content': video.malayalam_transcript,
        })

    return render(request, 'videos/upload_material.html', {'form': form, 'video': video})


# ---------------------------------------------------------------------------
# File text extraction helpers
# ---------------------------------------------------------------------------

def _extract_text(uploaded_file):
    name = uploaded_file.name.lower()
    data = uploaded_file.read()

    if name.endswith('.txt'):
        return data.decode('utf-8', errors='replace').strip()
    if name.endswith('.pdf'):
        return _extract_pdf(data)
    if name.endswith('.docx'):
        return _extract_docx(data)
    if name.endswith(('.pptx', '.ppt')):
        return _extract_pptx(data)

    raise ValueError(f'Unsupported file type: {uploaded_file.name}')


def _extract_pdf(data):
    import io as _io
    from pypdf import PdfReader
    reader = PdfReader(_io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ''
        if text.strip():
            pages.append(text.strip())
    return '\n\n'.join(pages)


def _extract_docx(data):
    import io as _io
    from docx import Document
    doc = Document(_io.BytesIO(data))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return '\n\n'.join(paras)


def _extract_pptx(data):
    import io as _io
    from pptx import Presentation
    prs = Presentation(_io.BytesIO(data))
    slides = []
    for slide in prs.slides:
        texts = [
            shape.text.strip()
            for shape in slide.shapes
            if hasattr(shape, 'text') and shape.text.strip()
        ]
        if texts:
            slides.append('\n'.join(texts))
    return '\n\n'.join(slides)


@login_required
def video_list_view(request, course_id):
    from courses.models import Course
    course = get_object_or_404(Course, id=course_id)
    videos = Video.objects.filter(course=course)
    return render(request, 'videos/video_list.html', {'course': course, 'videos': videos})


@login_required
def video_detail_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)

    # Students must be enrolled in the course to access content
    if request.user.role == User.Role.STUDENT:
        from courses.models import Enrollment
        enrolled = Enrollment.objects.filter(
            student=request.user, course=video.course, is_active=True
        ).exists()
        if not enrolled:
            messages.error(
                request,
                'You are not enrolled in this course. Contact your admin to get access.'
            )
            return redirect('course-detail', course_id=video.course_id)

    course_locked = _course_is_locked(video.course, request.user)

    # chatbot_enrolled: True for non-students, or enrolled students
    if request.user.role == User.Role.STUDENT:
        from courses.models import Enrollment
        chatbot_enrolled = Enrollment.objects.filter(
            student=request.user, course_id=video.course_id, is_active=True
        ).exists()
    else:
        chatbot_enrolled = True

    chunks = video.chunks.all()
    return render(request, 'videos/video_detail.html', {
        'video':             video,
        'chunks':            chunks,
        'course_locked':     course_locked,
        'signed_url':        _try_signed_url(video.video_key),
        'english_pdf_url':   _try_signed_url(video.english_pdf_key),
        'malayalam_pdf_url': _try_signed_url(video.malayalam_pdf_key),
        'chatbot_course_id': video.course_id,
        'chatbot_enrolled':  chatbot_enrolled,
    })


@_instructor_or_admin
def generate_quiz_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)

    if request.method != 'POST':
        return redirect('video-detail', video_id=video.id)

    if video.status != Video.Status.READY:
        messages.error(request, 'Quiz generation requires a fully processed video (status: READY).')
        return redirect('video-detail', video_id=video.id)

    try:
        from videos.tasks import generate_quiz
        generate_quiz.delay(video.id)
        messages.success(
            request,
            'Quiz generation started — you will be notified by email when the draft is ready.'
        )
    except Exception as exc:
        messages.warning(request, f'Task queue unavailable: {exc}')

    return redirect('video-detail', video_id=video.id)


@login_required
def video_status_api(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    return JsonResponse({
        'id':                video.id,
        'status':            video.status,
        'status_display':    video.get_status_display(),
        'chunk_count':       video.chunks.count(),
        'has_english_pdf':   bool(video.english_pdf_key),
        'has_malayalam_pdf': bool(video.malayalam_pdf_key),
    })
