import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def send_confirmation_emails(self, session_id):
    """Send HTML confirmation emails to both trainee and trainer."""
    from django.conf import settings as _s
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils import timezone
    from doubt_sessions.models import DoubtSession

    try:
        session = (
            DoubtSession.objects
            .select_related('student', 'instructor', 'slot')
            .get(id=session_id)
        )
    except DoubtSession.DoesNotExist:
        logger.error('send_confirmation_emails: session %s not found', session_id)
        return

    slot_local = timezone.localtime(session.slot.slot_datetime)
    ctx = {
        'session':    session,
        'student':    session.student,
        'instructor': session.instructor,
        'slot_time':  slot_local,
        'meet_url':   session.meet_url or '',
    }

    try:
        # ── Student email ─────────────────────────────────────────────────────
        if session.student.email:
            html = render_to_string('emails/session_confirmed_student.html', ctx)
            msg  = EmailMultiAlternatives(
                subject   = f'Interactive session confirmed — {slot_local.strftime("%b %d, %Y %I:%M %p")}',
                body      = (
                    f'Hi {session.student.first_name or session.student.username},\n\n'
                    f'Your interactive session with '
                    f'{session.instructor.get_full_name() or session.instructor.username} '
                    f'on {slot_local.strftime("%B %d, %Y at %I:%M %p")} is confirmed.\n\n'
                    f'Join: {session.meet_url or "(no link set)"}'
                ),
                from_email = _s.DEFAULT_FROM_EMAIL,
                to         = [session.student.email],
            )
            msg.attach_alternative(html, 'text/html')
            msg.send(fail_silently=True)

        # ── Instructor email ──────────────────────────────────────────────────
        if session.instructor.email:
            html = render_to_string('emails/session_confirmed_instructor.html', ctx)
            msg  = EmailMultiAlternatives(
                subject   = f'New interactive session booked — {slot_local.strftime("%b %d, %Y %I:%M %p")}',
                body      = (
                    f'Hi {session.instructor.first_name or session.instructor.username},\n\n'
                    f'Trainee {session.student.get_full_name() or session.student.username} '
                    f'has booked an interactive session on '
                    f'{slot_local.strftime("%B %d, %Y at %I:%M %p")}.\n\n'
                    f'Join: {session.meet_url or "(no link set)"}'
                ),
                from_email = _s.DEFAULT_FROM_EMAIL,
                to         = [session.instructor.email],
            )
            msg.attach_alternative(html, 'text/html')
            msg.send(fail_silently=True)

        logger.info('Confirmation emails sent for session %s', session_id)

    except Exception as exc:
        logger.exception('send_confirmation_emails failed (session %s): %s', session_id, exc)
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=2)
def send_reminder_email(self, session_id):
    """Send a 15-minute reminder to both parties if the session is still CONFIRMED."""
    from django.conf import settings as _s
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils import timezone
    from doubt_sessions.models import DoubtSession

    try:
        session = (
            DoubtSession.objects
            .select_related('student', 'instructor', 'slot')
            .get(id=session_id)
        )
    except DoubtSession.DoesNotExist:
        logger.error('send_reminder_email: session %s not found', session_id)
        return

    if session.status != DoubtSession.Status.CONFIRMED:
        logger.info('Skipping reminder for session %s (status=%s)', session_id, session.status)
        return

    slot_local = timezone.localtime(session.slot.slot_datetime)
    ctx = {
        'session':    session,
        'student':    session.student,
        'instructor': session.instructor,
        'slot_time':  slot_local,
        'meet_url':   session.meet_url or '',
        'minutes':    15,
    }

    try:
        html = render_to_string('emails/session_reminder.html', ctx)
        for email, name in [
            (session.student.email,    session.student.first_name    or session.student.username),
            (session.instructor.email, session.instructor.first_name or session.instructor.username),
        ]:
            if not email:
                continue
            msg = EmailMultiAlternatives(
                subject   = f'Reminder: session in 15 min — {slot_local.strftime("%I:%M %p")}',
                body      = (
                    f'Hi {name},\n\n'
                    f'Your interactive session starts in 15 minutes '
                    f'({slot_local.strftime("%B %d, %Y at %I:%M %p")}).\n\n'
                    f'Join now: {session.meet_url or "(no link set)"}'
                ),
                from_email = _s.DEFAULT_FROM_EMAIL,
                to         = [email],
            )
            msg.attach_alternative(html, 'text/html')
            msg.send(fail_silently=True)

        logger.info('Reminder sent for session %s', session_id)

    except Exception as exc:
        logger.exception('send_reminder_email failed (session %s): %s', session_id, exc)
        raise self.retry(exc=exc, countdown=30)
