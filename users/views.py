from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import CreateUserForm, ProfileForm, RegisterForm
from .models import User


def _role_home(user):
    if user.role == User.Role.ADMIN:
        return 'admin-dashboard'
    if user.role == User.Role.INSTRUCTOR:
        return 'instructor-dashboard'
    return 'student-dashboard'


def home_view(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    return redirect('login')


def register_view(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Account created. Please log in.')
            return redirect('login')
    else:
        form = RegisterForm()
    return render(request, 'users/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect(_role_home(user))
        messages.error(request, 'Invalid username or password.')
    return render(request, 'users/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


def _require_role(role):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if request.user.role != role:
                messages.error(request, 'You do not have permission to view that page.')
                return redirect(_role_home(request.user))
            return view_func(request, *args, **kwargs)
        wrapper.__name__ = view_func.__name__
        return wrapper
    return decorator


@_require_role(User.Role.STUDENT)
def student_dashboard(request):
    from courses.models import Enrollment
    from doubt_sessions.models import DoubtSession
    from quizzes.models import StudentQuizAttempt

    enrollments = (
        Enrollment.objects
        .filter(student=request.user, is_active=True)
        .select_related('course', 'course__instructor')
        .prefetch_related('course__videos')
    )
    chatbot_courses = [
        {'id': enrollment.course_id, 'title': enrollment.course.title}
        for enrollment in enrollments
    ]

    upcoming_session = (
        DoubtSession.objects
        .filter(
            student=request.user,
            status__in=[DoubtSession.Status.SELECTED, DoubtSession.Status.CONFIRMED],
        )
        .select_related('slot', 'instructor')
        .order_by('slot__slot_datetime')
        .first()
    )

    pending_request = (
        DoubtSession.objects
        .filter(
            student=request.user,
            status__in=[DoubtSession.Status.REQUESTED, DoubtSession.Status.POSTPONED],
        )
        .first()
    )

    slots_to_choose = list(
        DoubtSession.objects
        .filter(student=request.user, status=DoubtSession.Status.SELECTED)
        .prefetch_related('proposed_slots')
        .select_related('instructor', 'course')
    )

    recent_attempts = (
        StudentQuizAttempt.objects
        .filter(student=request.user)
        .select_related('quiz', 'quiz__video', 'quiz__video__course')
        .order_by('-completed_at')[:5]
    )

    return render(request, 'users/student_dashboard.html', {
        'enrollments':       enrollments,
        'upcoming_session':  upcoming_session,
        'pending_request':   pending_request,
        'slots_to_choose':   slots_to_choose,
        'recent_attempts':   recent_attempts,
        'chatbot_course_id': chatbot_courses[0]['id'] if chatbot_courses else 0,
        'chatbot_courses':   chatbot_courses,
        'chatbot_enrolled':  bool(chatbot_courses),
    })


@_require_role(User.Role.INSTRUCTOR)
def instructor_dashboard(request):
    from doubt_sessions.models import DoubtSession
    from quizzes.models import QuizDraft
    from videos.models import Video

    Video.objects.filter(
        course__instructor=request.user,
        status=Video.Status.UPLOADED,
    ).exclude(english_transcript='').update(status=Video.Status.PROCESSING)

    courses = list(
        request.user.courses_taught
        .filter(is_active=True)
        .prefetch_related('videos', 'enrollments')
    )

    pending_requests_count = DoubtSession.objects.filter(
        instructor=request.user, status=DoubtSession.Status.REQUESTED
    ).count()

    upcoming_sessions = (
        DoubtSession.objects
        .filter(
            instructor=request.user,
            slot__slot_datetime__gte=timezone.now(),
            status=DoubtSession.Status.CONFIRMED,
        )
        .select_related('student', 'slot')
        .order_by('slot__slot_datetime')[:5]
    )

    pending_drafts_count = QuizDraft.objects.filter(
        video__course__instructor=request.user,
        status=QuizDraft.Status.PENDING,
    ).count()
    approved_drafts_count = QuizDraft.objects.filter(
        video__course__instructor=request.user,
        status=QuizDraft.Status.APPROVED,
    ).count()

    recent_videos = list(
        Video.objects
        .filter(course__instructor=request.user)
        .select_related('course')
        .order_by('-created_at')[:6]
    )
    for video in recent_videos:
        video.sync_runtime_status()

    from courses.models import Enrollment
    total_students = Enrollment.objects.filter(
        course__instructor=request.user, is_active=True
    ).values('student').distinct().count()

    return render(request, 'users/instructor_dashboard.html', {
        'courses':                courses,
        'pending_requests_count': pending_requests_count,
        'upcoming_sessions':      upcoming_sessions,
        'pending_drafts_count':   pending_drafts_count,
        'approved_drafts_count':  approved_drafts_count,
        'reviewable_drafts_count': pending_drafts_count + approved_drafts_count,
        'recent_videos':          recent_videos,
        'total_students':         total_students,
    })


@_require_role(User.Role.ADMIN)
def admin_dashboard(request):
    from doubt_sessions.models import DoubtSession
    from videos.models import Video

    Video.objects.filter(
        status=Video.Status.UPLOADED,
    ).exclude(english_transcript='').update(status=Video.Status.PROCESSING)

    stats = {
        'total_users': User.objects.count(),
        'admins':       User.objects.filter(role=User.Role.ADMIN).count(),
        'instructors':  User.objects.filter(role=User.Role.INSTRUCTOR).count(),
        'students':     User.objects.filter(role=User.Role.STUDENT).count(),
    }
    try:
        from courses.models import Course
        stats['courses'] = Course.objects.count()
    except Exception:
        pass
    try:
        stats['videos'] = Video.objects.count()
    except Exception:
        pass
    try:
        from quizzes.models import QuizDraft
        stats['pending_quizzes'] = QuizDraft.objects.filter(
            status=QuizDraft.Status.PENDING
        ).count()
    except Exception:
        pass

    stats['open_doubt_sessions'] = DoubtSession.objects.filter(
        status__in=[
            DoubtSession.Status.REQUESTED,
            DoubtSession.Status.SELECTED,
            DoubtSession.Status.CONFIRMED,
        ]
    ).count()

    recent_videos = list(
        Video.objects.select_related('course').order_by('-created_at')[:5]
    )
    for video in recent_videos:
        video.sync_runtime_status()
    all_users = list(
        User.objects.order_by('-date_joined').select_related()[:20]
    )

    return render(request, 'users/admin_dashboard.html', {
        'stats':         stats,
        'recent_videos': recent_videos,
        'all_users':     all_users,
    })


@_require_role(User.Role.ADMIN)
def create_user_view(request):
    if request.method == 'POST':
        form = CreateUserForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f'User "{user.username}" ({user.get_role_display()}) created successfully.'
            )
            return redirect('admin-dashboard')
    else:
        form = CreateUserForm()
    return render(request, 'users/create_user.html', {'form': form})


@login_required
def profile_view(request):
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated.')
            return redirect('profile')
    else:
        form = ProfileForm(instance=request.user)
    return render(request, 'users/profile.html', {'form': form})
