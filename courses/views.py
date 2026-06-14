import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render

from users.decorators import role_required
from users.models import User
from .forms import BatchForm, CourseForm
from .models import Batch, BatchCourse, BatchStudent, Course, Enrollment

logger = logging.getLogger(__name__)

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


def _sync_batch_student_enrollments(batch, student):
    assigned_courses = Course.objects.filter(
        batch_assignments__batch=batch,
        batch_assignments__is_active=True,
        is_active=True,
    ).distinct()
    for course in assigned_courses:
        enrollment, _ = Enrollment.objects.get_or_create(student=student, course=course)
        if not enrollment.is_active:
            enrollment.is_active = True
            enrollment.save(update_fields=['is_active'])


def _sync_batch_course_enrollments(batch, course):
    active_students = User.objects.filter(
        batch_memberships__batch=batch,
        batch_memberships__is_active=True,
        role=User.Role.STUDENT,
    ).distinct()
    for student in active_students:
        enrollment, _ = Enrollment.objects.get_or_create(student=student, course=course)
        if not enrollment.is_active:
            enrollment.is_active = True
            enrollment.save(update_fields=['is_active'])


@login_required
def course_list(request):
    courses = Course.objects.filter(is_active=True).select_related('instructor')
    admin_manage_action = ''
    if request.user.role == User.Role.STUDENT:
        enrollment_qs = Enrollment.objects.filter(
            student=request.user,
            course_id=OuterRef('pk'),
            is_active=True,
        )
        courses = courses.annotate(is_enrolled=Exists(enrollment_qs))
    elif request.user.role == User.Role.INSTRUCTOR:
        courses = courses.filter(instructor=request.user)
    elif request.user.role == User.Role.ADMIN:
        admin_manage_action = (request.GET.get('manage') or '').strip().lower()
        if admin_manage_action not in {'edit', 'assign-student', 'assign-instructor'}:
            admin_manage_action = ''
        courses = courses.annotate(
            trainee_count=Count(
                'enrollments',
                filter=Q(enrollments__is_active=True),
                distinct=True,
            )
        )
    return render(request, 'courses/course_list.html', {
        'courses': courses,
        'admin_manage_action': admin_manage_action,
    })


@login_required
def course_detail(request, course_id):
    course = get_object_or_404(
        Course.objects.select_related('instructor'),
        id=course_id,
        is_active=True,
    )
    if request.user.role == User.Role.INSTRUCTOR and course.instructor_id != request.user.id:
        messages.error(request, 'You do not have permission to view that course.')
        return redirect('course-list')

    enrolled = (
        request.user.role == User.Role.STUDENT
        and Enrollment.objects.filter(
            student=request.user, course=course, is_active=True
        ).exists()
    )
    trainee_count = Enrollment.objects.filter(course=course, is_active=True).count()
    if request.user.role == User.Role.STUDENT and not enrolled:
        videos = course.videos.none()
        video_count = course.videos.count()
    else:
        videos = course.videos.prefetch_related('quizzes')
        video_count = videos.count()

    # Determine if course is locked for this instructor (1 video + study material uploaded)
    course_locked = False
    if request.user.role == User.Role.INSTRUCTOR:
        if videos.exists() and videos.first().english_transcript:
            course_locked = True

    return render(request, 'courses/course_detail.html', {
        'course':          course,
        'videos':          videos,
        'video_count':     video_count,
        'trainee_count':   trainee_count,
        'enrolled':        enrolled,
        'course_locked':   course_locked,
        'chatbot_course_id': course.id if enrolled else 0,
        'chatbot_courses':   [{'id': course.id, 'title': course.title}] if enrolled else [],
        'chatbot_enrolled':  enrolled,
    })


@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Trainers and admins only.')
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
    return render(request, 'courses/create_course.html', {
        'form': form,
        'page_heading': 'Create Course',
        'submit_label': 'Create Course',
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
def edit_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    from videos.models import Video

    if request.method == 'POST':
        form = CourseForm(request.POST, instance=course, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f'Course "{course.title}" updated.')
            return redirect('course-list',)
    else:
        form = CourseForm(instance=course, user=request.user)
    return render(request, 'courses/create_course.html', {
        'form': form,
        'course': course,
        'video_count': Video.objects.filter(course=course).count(),
        'enrollment_count': Enrollment.objects.filter(course=course, is_active=True).count(),
        'page_heading': 'Modify Course',
        'submit_label': 'Save Changes',
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
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
        return redirect(f"{reverse('course-list')}?manage=assign-student")
    students = User.objects.filter(role=User.Role.STUDENT)
    enrolled_ids = Enrollment.objects.filter(
        course=course, is_active=True
    ).values_list('student_id', flat=True)
    return render(request, 'courses/enroll_student.html', {
        'course':       course,
        'students':     students,
        'enrolled_ids': list(enrolled_ids),
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
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
                f'{instructor.username} assigned as trainer for {course.title}.'
            )
        else:
            course.instructor = None
            course.save(update_fields=['instructor'])
            messages.warning(request, f'Trainer removed from {course.title}.')
        return redirect(f"{reverse('course-list')}?manage=assign-instructor")
    instructors = User.objects.filter(role=User.Role.INSTRUCTOR)
    return render(request, 'courses/assign_instructor.html', {
        'course':      course,
        'instructors': instructors,
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
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


@role_required(User.Role.ADMIN, message='Admin access required.')
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
        courses = list(
            Course.objects.filter(instructor=request.user, is_active=True)
            .select_related('instructor')
        )
    else:
        courses = list(Course.objects.filter(is_active=True).select_related('instructor'))
    return render(request, 'courses/my_courses.html', {'courses': courses})


@role_required(User.Role.ADMIN, message='Admin access required.')
def batch_list(request):
    admin_manage_action = (request.GET.get('manage') or '').strip().lower()
    if admin_manage_action not in {'assign-students', 'assign-courses'}:
        admin_manage_action = ''
    batches = Batch.objects.filter(is_active=True).annotate(
        trainee_count=Count(
            'student_memberships',
            filter=Q(student_memberships__is_active=True),
            distinct=True,
        ),
        course_count=Count(
            'course_assignments',
            filter=Q(course_assignments__is_active=True),
            distinct=True,
        ),
    )
    return render(request, 'courses/batch_list.html', {
        'batches': batches,
        'admin_manage_action': admin_manage_action,
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
def create_batch(request):
    if request.method == 'POST':
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save()
            messages.success(request, f'Batch "{batch.name}" created.')
            return redirect('batch-list')
    else:
        form = BatchForm()
    return render(request, 'courses/create_batch.html', {
        'form': form,
        'page_heading': 'Create Batch',
        'submit_label': 'Create Batch',
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
def assign_students_to_batch(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id, is_active=True)
    students = User.objects.filter(role=User.Role.STUDENT).order_by('username')
    assigned_ids = list(
        BatchStudent.objects.filter(batch=batch, is_active=True).values_list('student_id', flat=True)
    )

    if request.method == 'POST':
        student_ids = request.POST.getlist('student_ids')
        valid_students = students.filter(id__in=student_ids)
        added_count = 0
        for student in valid_students:
            membership, created = BatchStudent.objects.get_or_create(batch=batch, student=student)
            if created or not membership.is_active:
                membership.is_active = True
                membership.save(update_fields=['is_active'])
                added_count += 1
            _sync_batch_student_enrollments(batch, student)
        if added_count:
            messages.success(request, f'{added_count} trainee(s) added to {batch.name}.')
        else:
            messages.info(request, 'No new trainees were added to this batch.')
        return redirect(f'{reverse("batch-list")}?manage=assign-students')

    return render(request, 'courses/assign_batch_students.html', {
        'batch': batch,
        'students': students,
        'assigned_ids': assigned_ids,
    })


@role_required(User.Role.ADMIN, message='Admin access required.')
def assign_courses_to_batch(request, batch_id):
    batch = get_object_or_404(Batch, id=batch_id, is_active=True)
    courses = Course.objects.filter(is_active=True).select_related('instructor').order_by('title')
    assigned_ids = list(
        BatchCourse.objects.filter(batch=batch, is_active=True).values_list('course_id', flat=True)
    )

    if request.method == 'POST':
        course_ids = request.POST.getlist('course_ids')
        valid_courses = courses.filter(id__in=course_ids)
        added_count = 0
        for course in valid_courses:
            assignment, created = BatchCourse.objects.get_or_create(batch=batch, course=course)
            if created or not assignment.is_active:
                assignment.is_active = True
                assignment.save(update_fields=['is_active'])
                added_count += 1
            _sync_batch_course_enrollments(batch, course)
        if added_count:
            messages.success(request, f'{added_count} course(s) assigned to {batch.name}.')
        else:
            messages.info(request, 'No new courses were assigned to this batch.')
        return redirect(f'{reverse("batch-list")}?manage=assign-courses')

    return render(request, 'courses/assign_batch_courses.html', {
        'batch': batch,
        'courses': courses,
        'assigned_ids': assigned_ids,
    })
