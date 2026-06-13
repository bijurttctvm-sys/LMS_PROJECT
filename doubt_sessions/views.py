import calendar as _calendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from users.models import User
from .models import DoubtSession, InstructorSlot, ProposedSlot


# Access decorators

def _instructor_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.role != User.Role.INSTRUCTOR:
            messages.error(request, 'Instructor access required.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def _student_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.role != User.Role.STUDENT:
            messages.error(request, 'Student access required.')
            return redirect('home')
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


# Student views

@_student_required
def request_session(request):
    """Student submits a doubt-clearing request for one of their enrolled courses."""
    from courses.models import Enrollment

    eligible, next_eligible_date, active_session = DoubtSession.is_eligible(request.user)

    if request.method == 'POST':
        if not eligible:
            messages.error(request, 'You are not currently eligible to request a session.')
            return redirect('request-session')

        course_id = request.POST.get('course_id', '').strip()
        message   = request.POST.get('message', '').strip()

        try:
            enrollment = (
                Enrollment.objects
                .select_related('course', 'course__instructor')
                .get(student=request.user, course_id=course_id, is_active=True)
            )
        except Enrollment.DoesNotExist:
            messages.error(request, 'Invalid course selection.')
            return redirect('request-session')

        course = enrollment.course
        if not course.instructor:
            messages.error(request, 'This course has no assigned instructor yet.')
            return redirect('request-session')

        DoubtSession.objects.create(
            student         = request.user,
            instructor      = course.instructor,
            course          = course,
            request_message = message,
            status          = DoubtSession.Status.REQUESTED,
        )
        messages.success(
            request,
            'Doubt session request submitted. Instructor will propose time slots soon.'
        )
        return redirect('my-sessions')

    enrollments = (
        Enrollment.objects
        .filter(student=request.user, is_active=True)
        .select_related('course', 'course__instructor')
        .order_by('course__title')
    )
    return render(request, 'doubt_sessions/request_session.html', {
        'eligible':           eligible,
        'next_eligible_date': next_eligible_date,
        'active_session':     active_session,
        'enrollments':        enrollments,
    })


@_student_required
def choose_slot(request, session_id):
    """Student picks one of the 3 time slots proposed by the instructor."""
    session = get_object_or_404(
        DoubtSession,
        id=session_id,
        student=request.user,
        status=DoubtSession.Status.SELECTED,
    )

    if request.method == 'POST':
        proposed_id = request.POST.get('proposed_slot_id', '').strip()
        try:
            proposed = session.proposed_slots.get(id=proposed_id)
        except ProposedSlot.DoesNotExist:
            messages.error(request, 'Please select a valid time slot.')
            return redirect('choose-slot', session_id=session_id)

        with transaction.atomic():
            instructor_slot = InstructorSlot.objects.create(
                instructor    = session.instructor,
                slot_datetime = proposed.slot_datetime,
                is_available  = False,
            )
            proposed.is_selected = True
            proposed.save(update_fields=['is_selected'])

            session.slot     = instructor_slot
            session.status   = DoubtSession.Status.CONFIRMED
            session.meet_url = session.instructor.google_meet_link or ''
            session.save(update_fields=['slot', 'status', 'meet_url'])

        local_dt = timezone.localtime(proposed.slot_datetime)

        try:
            from doubt_sessions.tasks import send_confirmation_emails, send_reminder_email
            from datetime import timedelta as _timedelta
            send_confirmation_emails.delay(session.id)
            reminder_eta = proposed.slot_datetime - _timedelta(minutes=15)
            if reminder_eta > timezone.now():
                send_reminder_email.apply_async(args=[session.id], eta=reminder_eta)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning('Task queue unavailable: %s', exc)

        messages.success(
            request,
            f'Session confirmed for {local_dt.strftime("%B %d, %Y at %I:%M %p")}!'
        )
        return redirect('my-sessions')

    proposed_slots = session.proposed_slots.all()
    return render(request, 'doubt_sessions/choose_slot.html', {
        'session':        session,
        'proposed_slots': proposed_slots,
    })


@login_required
def my_sessions(request):
    """Student: full session history with pending slot-choice alerts."""
    choose_sessions = []
    if request.user.role == User.Role.STUDENT:
        choose_sessions = list(
            DoubtSession.objects
            .filter(student=request.user, status=DoubtSession.Status.SELECTED)
            .prefetch_related('proposed_slots')
            .select_related('instructor', 'course')
        )

    sessions = (
        DoubtSession.objects
        .filter(student=request.user)
        .select_related('instructor', 'slot', 'course')
        .order_by('-created_at')
    )
    return render(request, 'doubt_sessions/my_sessions.html', {
        'sessions':        sessions,
        'choose_sessions': choose_sessions,
        'now':             timezone.now(),
    })


# Instructor views

@_instructor_required
def instructor_sessions(request):
    """Instructor: pending requests + upcoming/past sessions."""
    now = timezone.now()

    pending_requests = (
        DoubtSession.objects
        .filter(
            instructor=request.user,
            status__in=[DoubtSession.Status.REQUESTED, DoubtSession.Status.POSTPONED],
        )
        .select_related('student', 'course')
        .order_by('created_at')
    )
    slots_proposed = (
        DoubtSession.objects
        .filter(instructor=request.user, status=DoubtSession.Status.SELECTED)
        .select_related('student', 'course')
        .prefetch_related('proposed_slots')
        .order_by('created_at')
    )
    upcoming = (
        DoubtSession.objects
        .filter(
            instructor=request.user,
            slot__slot_datetime__gte=now,
            status=DoubtSession.Status.CONFIRMED,
        )
        .select_related('student', 'slot', 'course')
        .order_by('slot__slot_datetime')
    )
    past = (
        DoubtSession.objects
        .filter(instructor=request.user)
        .exclude(
            status__in=[
                DoubtSession.Status.REQUESTED,
                DoubtSession.Status.SELECTED,
                DoubtSession.Status.POSTPONED,
            ]
        )
        .select_related('student', 'slot', 'course')
        .order_by('-created_at')[:30]
    )
    return render(request, 'doubt_sessions/instructor_sessions.html', {
        'pending_requests': pending_requests,
        'slots_proposed':   slots_proposed,
        'upcoming':         upcoming,
        'past':             past,
    })


@_instructor_required
def propose_slots(request, session_id):
    """Instructor proposes 3 time slots for a requested doubt session."""
    session = get_object_or_404(
        DoubtSession,
        id=session_id,
        instructor=request.user,
        status__in=[DoubtSession.Status.REQUESTED, DoubtSession.Status.POSTPONED],
    )

    if request.method == 'POST':
        slot_datetimes = []
        errors = []
        for i in range(1, 4):
            raw = request.POST.get(f'slot_{i}', '').strip()
            if not raw:
                errors.append(f'Slot {i} is required.')
                continue
            try:
                naive_dt = datetime.strptime(raw, '%Y-%m-%dT%H:%M')
                aware_dt = timezone.make_aware(naive_dt)
                if aware_dt <= timezone.now():
                    errors.append(f'Slot {i} must be in the future.')
                else:
                    slot_datetimes.append(aware_dt)
            except ValueError:
                errors.append(f'Slot {i}: invalid date/time format.')

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, 'doubt_sessions/propose_slots.html', {'session': session})

        if len(slot_datetimes) != 3:
            messages.error(request, 'Please provide all 3 time slots.')
            return render(request, 'doubt_sessions/propose_slots.html', {'session': session})

        session.proposed_slots.all().delete()
        for dt in slot_datetimes:
            ProposedSlot.objects.create(session=session, slot_datetime=dt)

        session.status = DoubtSession.Status.SELECTED
        session.save(update_fields=['status'])

        name = session.student.get_full_name() or session.student.username
        messages.success(
            request,
            f'3 time slots proposed for {name}. Waiting for their selection.'
        )
        return redirect('instructor-sessions')

    return render(request, 'doubt_sessions/propose_slots.html', {'session': session})


@_instructor_required
def mark_outcome(request, session_id):
    """Instructor: mark a confirmed session as attended, not attended, or postponed."""
    session = get_object_or_404(DoubtSession, id=session_id, instructor=request.user)

    if request.method == 'POST':
        outcome = request.POST.get('outcome', '').strip()
        if session.status != DoubtSession.Status.CONFIRMED:
            messages.error(request, 'Only confirmed sessions can be updated here.')
            return redirect('instructor-sessions')

        if outcome == 'attended':
            session.status           = DoubtSession.Status.ATTENDED
            session.last_attended_at = timezone.now()
            session.save(update_fields=['status', 'last_attended_at'])
            messages.success(request, 'Session marked as attended.')
        elif outcome == 'no_show':
            session.status = DoubtSession.Status.NO_SHOW
            session.save(update_fields=['status'])
            if session.slot:
                InstructorSlot.objects.filter(id=session.slot_id).update(is_available=True)
            messages.warning(request, 'Session marked as not attended.')
        elif outcome == 'postponed':
            if session.instructor_postponed_once:
                messages.error(request, 'This session has already been postponed once.')
                return redirect('instructor-sessions')

            if session.slot_id:
                InstructorSlot.objects.filter(id=session.slot_id).update(is_available=True)
            session.proposed_slots.all().delete()
            session.slot = None
            session.meet_url = ''
            session.status = DoubtSession.Status.POSTPONED
            session.instructor_postponed_once = True
            session.save(
                update_fields=[
                    'slot',
                    'meet_url',
                    'status',
                    'instructor_postponed_once',
                ]
            )
            messages.warning(
                request,
                'Session postponed. Please propose 3 new time slots for the student.',
            )
        else:
            messages.error(request, 'Invalid outcome.')

    return redirect('instructor-sessions')


# Admin views

@_admin_required
def admin_sessions(request):
    """Admin: view all doubt sessions across the platform."""
    status_filter = request.GET.get('status', '')
    qs = (
        DoubtSession.objects
        .all()
        .select_related('student', 'instructor', 'slot', 'course')
        .order_by('-created_at')
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    return render(request, 'doubt_sessions/admin_sessions.html', {
        'sessions':       qs,
        'status_filter':  status_filter,
        'status_choices': DoubtSession.Status.choices,
    })


@_admin_required
def admin_close_session(request, session_id):
    """Admin: close a doubt session as COMPLETED or CANCELLED."""
    session = get_object_or_404(DoubtSession, id=session_id)
    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        if action == 'completed':
            session.status = DoubtSession.Status.COMPLETED
            session.save(update_fields=['status'])
            messages.success(request, f'Session #{session_id} marked as completed.')
        elif action == 'cancelled':
            session.status = DoubtSession.Status.CANCELLED
            session.save(update_fields=['status'])
            if session.slot:
                session.slot.is_available = True
                session.slot.save(update_fields=['is_available'])
            messages.warning(request, f'Session #{session_id} cancelled.')
        else:
            messages.error(request, 'Invalid action.')
    return redirect('admin-sessions')
