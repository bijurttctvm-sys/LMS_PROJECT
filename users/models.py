from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model for the multilingual LMS.

    Extends Django's AbstractUser to add a role, instructor meeting link,
    profile picture, phone and language preference.
    """

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        INSTRUCTOR = 'instructor', 'Instructor'
        STUDENT = 'student', 'Student'

    class Language(models.TextChoices):
        ENGLISH = 'en', 'English'
        MALAYALAM = 'ml', 'Malayalam'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
    )
    google_meet_link = models.URLField(
        blank=True,
        null=True,
        help_text='Personal Google Meet link (instructors only).',
    )
    profile_picture = models.ImageField(
        upload_to='profiles/',
        blank=True,
        null=True,
    )
    phone = models.CharField(max_length=15, blank=True, null=True)
    preferred_language = models.CharField(
        max_length=2,
        choices=Language.choices,
        default=Language.ENGLISH,
    )

    def is_admin(self):
        return self.role == self.Role.ADMIN

    def is_instructor(self):
        return self.role == self.Role.INSTRUCTOR

    def is_student(self):
        return self.role == self.Role.STUDENT

    def __str__(self):
        return f'{self.get_username()} ({self.get_role_display()})'
