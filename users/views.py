from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .decorators import role_home, role_required
from .forms import ChangePasswordForm, CreateUserForm, ProfileForm, RegisterForm
from .models import User
from .security import clear_failed_logins, is_login_rate_limited, register_failed_login


def _role_home(user):
    return role_home(user)


_MANAGEABLE_ADMIN_ROLES = {
    User.Role.INSTRUCTOR,
    User.Role.STUDENT,
}


def _format_lockout_duration(seconds):
    if seconds >= 60:
        minutes = max(1, round(seconds / 60))
        unit = 'minute' if minutes == 1 else 'minutes'
        return f'{minutes} {unit}'
    unit = 'second' if seconds == 1 else 'seconds'
    return f'{seconds} {unit}'


def _safe_next_url(request, fallback_url):
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url


def _user_management_config(role):
    role_config = {
        User.Role.STUDENT: {
            'page_title': 'Trainee Management',
            'card_title': 'Manage Trainees',
            'kicker': 'Trainee operations',
            'create_heading': 'Enroll Trainee',
            'empty_message': 'No trainees have been enrolled yet.',
            'delete_label': 'Delete Trainee',
            'toggle_activate': 'Activate Trainee',
            'toggle_deactivate': 'Deactivate Trainee',
        },
        User.Role.INSTRUCTOR: {
            'page_title': 'Trainer Management',
            'card_title': 'Manage Trainers',
            'kicker': 'Trainer operations',
            'create_heading': 'Enroll Trainer',
            'empty_message': 'No trainers have been enrolled yet.',
            'delete_label': 'Delete Trainer',
            'toggle_activate': 'Activate Trainer',
            'toggle_deactivate': 'Deactivate Trainer',
        },
    }
    return role_config.get(role)


def _management_url_for_role(role):
    return reverse('manage-users', args=[role])


def _managed_user_has_related_records(user):
    from courses.models import BatchStudent, Course, Enrollment
    from doubt_sessions.models import DoubtSession, InstructorSlot
    from quizzes.models import StudentQuizAttempt

    if user.role == User.Role.STUDENT:
        return any((
            Enrollment.objects.filter(student=user).exists(),
            BatchStudent.objects.filter(student=user).exists(),
            DoubtSession.objects.filter(student=user).exists(),
            StudentQuizAttempt.objects.filter(student=user).exists(),
        ))

    if user.role == User.Role.INSTRUCTOR:
        return any((
            Course.objects.filter(instructor=user).exists(),
            DoubtSession.objects.filter(instructor=user).exists(),
            InstructorSlot.objects.filter(instructor=user).exists(),
        ))

    return False


def home_view(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    return redirect('login')


@never_cache
@sensitive_post_parameters('password1', 'password2')
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


@never_cache
@sensitive_post_parameters('password')
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        rate_limited, remaining_seconds = is_login_rate_limited(request, username)
        if rate_limited:
            messages.error(
                request,
                'Too many failed login attempts. '
                f'Please try again in {_format_lockout_duration(remaining_seconds)}.'
            )
            return render(request, 'users/login.html')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            clear_failed_logins(request, username)
            login(request, user)
            return redirect(_role_home(user))
        register_failed_login(request, username)
        rate_limited, remaining_seconds = is_login_rate_limited(request, username)
        if rate_limited:
            messages.error(
                request,
                'Too many failed login attempts. '
                f'Please try again in {_format_lockout_duration(remaining_seconds)}.'
            )
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'users/login.html')


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


@role_required(User.Role.STUDENT)
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
        'max_sessions_per_course': DoubtSession.MAX_SESSIONS_PER_COURSE,
        'chatbot_course_id': chatbot_courses[0]['id'] if chatbot_courses else 0,
        'chatbot_courses':   chatbot_courses,
        'chatbot_enrolled':  bool(chatbot_courses),
    })


@role_required(User.Role.INSTRUCTOR)
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
    first_pending_video_id = (
        QuizDraft.objects
        .filter(
            video__course__instructor=request.user,
            status=QuizDraft.Status.PENDING,
        )
        .order_by('created_at')
        .values_list('video_id', flat=True)
        .first()
    )
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
        'first_pending_video_id': first_pending_video_id,
        'approved_drafts_count':  approved_drafts_count,
        'reviewable_drafts_count': pending_drafts_count + approved_drafts_count,
        'recent_videos':          recent_videos,
        'total_students':         total_students,
    })


@role_required(User.Role.ADMIN)
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
    all_users = list(User.objects.order_by('-date_joined')[:20])

    return render(request, 'users/admin_dashboard.html', {
        'stats':         stats,
        'recent_videos': recent_videos,
        'all_users':     all_users,
    })


@role_required(User.Role.ADMIN)
def manage_users_view(request, role):
    role = (role or '').strip().lower()
    config = _user_management_config(role)
    if not config:
        messages.error(request, 'Select trainee or trainer management.')
        return redirect('admin-dashboard')

    search_query = (request.GET.get('q') or '').strip()
    status_filter = (request.GET.get('status') or '').strip().lower()
    if status_filter not in {'active', 'inactive'}:
        status_filter = ''

    users = (
        User.objects
        .filter(role=role)
        .annotate(
            active_course_count=Count(
                'enrollments' if role == User.Role.STUDENT else 'courses_taught',
                filter=Q(enrollments__is_active=True) if role == User.Role.STUDENT else Q(courses_taught__is_active=True),
                distinct=True,
            ),
            active_batch_count=Count(
                'batch_memberships',
                filter=Q(batch_memberships__is_active=True),
                distinct=True,
            ) if role == User.Role.STUDENT else Count('slots', distinct=True),
        )
        .order_by('-is_active', 'username')
    )

    if search_query:
        users = users.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )
    if status_filter == 'active':
        users = users.filter(is_active=True)
    elif status_filter == 'inactive':
        users = users.filter(is_active=False)

    return render(request, 'users/manage_users.html', {
        'managed_role': role,
        'config': config,
        'managed_users': users,
        'search_query': search_query,
        'status_filter': status_filter,
        'create_user_url': f"{reverse('create-user')}?role={role}",
        'stats_total': users.count(),
        'stats_active': users.filter(is_active=True).count(),
        'stats_inactive': users.filter(is_active=False).count(),
    })


@role_required(User.Role.ADMIN)
@sensitive_post_parameters('password1', 'password2')
def create_user_view(request):
    requested_role = (
        request.GET.get('role')
        or request.POST.get('role')
        or ''
    ).strip().lower()
    role_titles = {
        User.Role.INSTRUCTOR: ('Enroll Trainer', 'Create Trainer Account'),
        User.Role.STUDENT: ('Enroll Trainee', 'Create Trainee Account'),
    }
    if request.method == 'POST':
        form = CreateUserForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f'User "{user.username}" ({user.get_role_display()}) created successfully.'
            )
            if user.role in _MANAGEABLE_ADMIN_ROLES:
                return redirect(_management_url_for_role(user.role))
            return redirect('admin-dashboard')
    else:
        initial = {}
        if requested_role in (User.Role.INSTRUCTOR, User.Role.STUDENT):
            initial['role'] = requested_role
        form = CreateUserForm(initial=initial)
    page_heading, card_heading = role_titles.get(
        requested_role,
        ('Create User', 'Create User Account'),
    )
    return render(request, 'users/create_user.html', {
        'form': form,
        'page_heading': page_heading,
        'card_heading': card_heading,
        'cancel_url': (
            _management_url_for_role(requested_role)
            if requested_role in _MANAGEABLE_ADMIN_ROLES
            else reverse('admin-dashboard')
        ),
    })


@role_required(User.Role.ADMIN)
@require_POST
def toggle_user_active_view(request, user_id):
    managed_user = get_object_or_404(User, pk=user_id)
    if managed_user.role not in _MANAGEABLE_ADMIN_ROLES:
        messages.error(request, 'Only trainees and trainers can be managed here.')
        return redirect('admin-dashboard')
    if managed_user.pk == request.user.pk:
        messages.error(request, 'You cannot change your own administrator account here.')
        return redirect(_management_url_for_role(managed_user.role))

    managed_user.is_active = not managed_user.is_active
    managed_user.save(update_fields=['is_active'])
    state_label = 'activated' if managed_user.is_active else 'deactivated'
    messages.success(
        request,
        f'{managed_user.get_role_display()} "{managed_user.username}" {state_label} successfully.',
    )
    return redirect(_safe_next_url(request, _management_url_for_role(managed_user.role)))


@role_required(User.Role.ADMIN)
@require_POST
def delete_user_view(request, user_id):
    managed_user = get_object_or_404(User, pk=user_id)
    if managed_user.role not in _MANAGEABLE_ADMIN_ROLES:
        messages.error(request, 'Only trainees and trainers can be removed here.')
        return redirect('admin-dashboard')
    if managed_user.pk == request.user.pk:
        messages.error(request, 'You cannot remove your own administrator account.')
        return redirect(_management_url_for_role(managed_user.role))

    fallback_url = _management_url_for_role(managed_user.role)
    role_label = managed_user.get_role_display()
    username = managed_user.username

    if _managed_user_has_related_records(managed_user):
        if managed_user.is_active:
            managed_user.is_active = False
            managed_user.save(update_fields=['is_active'])
            messages.warning(
                request,
                f'{role_label} "{username}" was deactivated instead of permanently deleted so related records remain intact.',
            )
        else:
            messages.info(
                request,
                f'{role_label} "{username}" already has preserved related records and remains inactive.',
            )
        return redirect(_safe_next_url(request, fallback_url))

    managed_user.delete()
    messages.success(request, f'{role_label} "{username}" deleted successfully.')
    return redirect(_safe_next_url(request, fallback_url))


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


@login_required
@never_cache
@sensitive_post_parameters('old_password', 'new_password1', 'new_password2')
def change_password_view(request):
    if request.method == 'POST':
        form = ChangePasswordForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password has been updated successfully.')
            return redirect('profile')
    else:
        form = ChangePasswordForm(user=request.user)
    return render(request, 'users/change_password.html', {'form': form})
