from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class InstructorSlot(models.Model):
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='slots',
        limit_choices_to={'role': 'instructor'},
    )
    slot_datetime = models.DateTimeField()
    is_available  = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['slot_datetime']

    def __str__(self):
        local = timezone.localtime(self.slot_datetime)
        return f"{self.instructor.username} @ {local.strftime('%Y-%m-%d %H:%M')}"


class DoubtSession(models.Model):
    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        SELECTED  = 'selected',  'Slots Proposed'
        CONFIRMED = 'confirmed', 'Confirmed'
        POSTPONED = 'postponed', 'Postponed by Instructor'
        ATTENDED  = 'attended',  'Attended'
        NO_SHOW   = 'no_show',   'Not Attended'
        CANCELLED = 'cancelled', 'Cancelled'
        COMPLETED = 'completed', 'Completed'

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doubt_sessions_as_student',
        limit_choices_to={'role': 'student'},
    )
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doubt_sessions_as_instructor',
    )
    slot = models.OneToOneField(
        InstructorSlot,
        on_delete=models.PROTECT,
        related_name='session',
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        'courses.Course',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='doubt_sessions',
    )
    request_message  = models.TextField(blank=True)
    meet_url         = models.URLField(blank=True, null=True)
    instructor_postponed_once = models.BooleanField(default=False)
    status           = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CONFIRMED, db_index=True
    )
    last_attended_at = models.DateTimeField(blank=True, null=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.student.username} w/ {self.instructor.username} [{self.status}]"

    @classmethod
    def is_eligible(cls, student):
        """
        Returns (eligible, next_eligible_date, active_session).
        - Has REQUESTED/SELECTED/CONFIRMED session -> not eligible
        - Had ATTENDED session within 30 days -> not eligible
        - Otherwise -> eligible
        """
        active = (
            cls.objects
            .filter(student=student, status__in=[
                cls.Status.REQUESTED,
                cls.Status.SELECTED,
                cls.Status.CONFIRMED,
                cls.Status.POSTPONED,
            ])
            .select_related('slot', 'slot__instructor', 'instructor', 'course')
            .first()
        )
        if active:
            return False, None, active

        thirty_days_ago = timezone.now() - timedelta(days=30)
        last_attended = (
            cls.objects
            .filter(
                student=student,
                status=cls.Status.ATTENDED,
                last_attended_at__gte=thirty_days_ago,
            )
            .order_by('-last_attended_at')
            .first()
        )
        if last_attended:
            next_eligible = last_attended.last_attended_at + timedelta(days=30)
            return False, next_eligible, None

        return True, None, None


class ProposedSlot(models.Model):
    session       = models.ForeignKey(DoubtSession, on_delete=models.CASCADE, related_name='proposed_slots')
    slot_datetime = models.DateTimeField()
    is_selected   = models.BooleanField(default=False)

    class Meta:
        ordering = ['slot_datetime']

    def __str__(self):
        return f"Proposed for session {self.session_id} @ {self.slot_datetime}"
