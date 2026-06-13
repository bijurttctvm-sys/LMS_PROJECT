import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from users.models import User
from .forms import CourseForm
from .models import Course, Enrollment

logger = logging.getLogger(__name__)


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


def _admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.role != User.Role.ADMIN:
            messages.error(request, 'Admin access required.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def _delete_course_assets(course, delete_videos=True):
    """Remove external assets for a course and optionally delete its videos."""
    from videos.models import Video

    videos = Video.objects.filter(course=course)
    for video in videos:
        try:
            from utils.pinecone_client import delete_video_chunks
            delete_video_chunks(video.id)
        except Exception as exc:
            logger.warning('Pinecone delete failed for video %s: %s', video.id, exc)
        try:
            from utils.r2_storage import delete_file
            for key in (video.video_key, video.english_pdf_key, video.malayalam_pdf_key):
                if key:
                    delete_file(key)
        except Exception as exc:
            logger.warning('R2 delete failed for video %s: %s', video.id, exc)

    deleted_count = videos.count()
    if delete_videos:
        videos.delete()  # cascades to TranscriptChunk, QuizDraft, Quiz
    return deleted_count


@login_required
def course_list(request):
    if request.user.role == User.Role.STUDENT:
        enrolled_ids = Enrollment.objects.filter(
            student=request.user, is_active=True
        ).values_list('course_id', flat=True)
        courses = Course.objects.filter(id__in=enrolled_ids).select_related('instructor')
    else:
        courses = Course.objects.filter(is_active=True).select_related('instructor')
    return render(request, 'courses/course_list.html', {'courses': courses})


@login_required
def course_detail(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    # Students can only view courses they are enrolled in
    if request.user.role == User.Role.STUDENT:
        if not Enrollment.objects.filter(
            student=request.user, course=course, is_active=True
        ).exists():
            messages.error(request, 'You are not enrolled in this course.')
            return redirect('course-list')

    videos = course.videos.all()
    enrolled = (
        request.user.role == User.Role.STUDENT
        and Enrollment.objects.filter(
            student=request.user, course=course, is_active=True
        ).exists()
    )

    # Determine if course is locked for this instructor (1 video + study material uploaded)
    course_locked = False
    if request.user.role == User.Role.INSTRUCTOR:
        if videos.exists() and videos.first().english_transcript:
            course_locked = True

    return render(request, 'courses/course_detail.html', {
        'course':          course,
        'videos':          videos,
        'enrolled':        enrolled,
        'course_locked':   course_locked,
        'chatbot_course_id': course.id if enrolled else 0,
        'chatbot_courses':   [{'id': course.id, 'title': course.title}] if enrolled else [],
        'chatbot_enrolled':  enrolled,
    })


@_instructor_or_admin
def create_course(request):
    if request.method == 'POST':
        form = CourseForm(request.POST, user=request.user)
        if form.is_valid():
            course = form.save(commit=False)
            if request.user.role == User.Role.INSTRUCTOR:
                course.instructor = request.user
            course.save()
            messages.success(request, f'Course "{course.title}" created.')
            return redirect('course-detail', course_id=course.id)
    else:
        form = CourseForm(user=request.user)
    return render(request, 'courses/create_course.html', {'form': form})


@_admin_required
def enroll_student(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    if request.method == 'POST':
        student_id = request.POST.get('student_id')
        student = get_object_or_404(User, id=student_id, role=User.Role.STUDENT)
        enrollment, created = Enrollment.objects.get_or_create(
            student=student, course=course
        )
        enrollment.is_active = True
        enrollment.save(update_fields=['is_active'])
        messages.success(request, f'{student.username} enrolled in {course.title}.')
        return redirect('course-detail', course_id=course.id)
    students = User.objects.filter(role=User.Role.STUDENT)
    enrolled_ids = Enrollment.objects.filter(
        course=course, is_active=True
    ).values_list('student_id', flat=True)
    return render(request, 'courses/enroll_student.html', {
        'course':       course,
        'students':     students,
        'enrolled_ids': list(enrolled_ids),
    })


@_admin_required
def assign_instructor(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    if request.method == 'POST':
        instructor_id = request.POST.get('instructor_id', '').strip()
        if instructor_id:
            instructor = get_object_or_404(User, id=instructor_id, role=User.Role.INSTRUCTOR)
            course.instructor = instructor
            course.save(update_fields=['instructor'])
            messages.success(
                request,
                f'{instructor.username} assigned as instructor for {course.title}.'
            )
        else:
            course.instructor = None
            course.save(update_fields=['instructor'])
            messages.warning(request, f'Instructor removed from {course.title}.')
        return redirect('course-detail', course_id=course.id)
    instructors = User.objects.filter(role=User.Role.INSTRUCTOR)
    return render(request, 'courses/assign_instructor.html', {
        'course':      course,
        'instructors': instructors,
    })


@_admin_required
def delete_course_content(request, course_id):
    """Delete all videos, study material, Pinecone vectors, and quizzes for a course."""
    course = get_object_or_404(Course, id=course_id)

    if request.method == 'POST':
        deleted_count = _delete_course_assets(course)

        messages.success(
            request,
            f'All content for "{course.title}" deleted '
            f'({deleted_count} video(s) and all related quizzes).'
        )
        return redirect('course-detail', course_id=course.id)

    from videos.models import Video
    video_count = Video.objects.filter(course=course).count()
    return render(request, 'courses/delete_content.html', {
        'course':      course,
        'video_count': video_count,
    })


@_admin_required
def delete_course(request, course_id):
    """Delete a course together with all its content and enrollments."""
    course = get_object_or_404(Course, id=course_id)

    if request.method == 'POST':
        course_title = course.title
        deleted_count = _delete_course_assets(course)
        course.delete()
        messages.success(
            request,
            f'Course "{course_title}" deleted permanently '
            f'({deleted_count} video(s), all content, and all enrollments removed).'
        )
        return redirect('course-list')

    from videos.models import Video
    video_count = Video.objects.filter(course=course).count()
    enrollment_count = course.enrollments.filter(is_active=True).count()
    return render(request, 'courses/delete_course.html', {
        'course':            course,
        'video_count':       video_count,
        'enrollment_count':  enrollment_count,
    })


@login_required
def my_courses(request):
    if request.user.role == User.Role.STUDENT:
        enrollments = Enrollment.objects.filter(
            student=request.user, is_active=True
        ).select_related('course__instructor')
        courses = [e.course for e in enrollments]
    elif request.user.role == User.Role.INSTRUCTOR:
        courses = list(Course.objects.filter(instructor=request.user, is_active=True))
    else:
        courses = list(Course.objects.filter(is_active=True))
    return render(request, 'courses/my_courses.html', {'courses': courses})
