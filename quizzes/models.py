from django.conf import settings
from django.db import models


class QuizDraft(models.Model):
    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    OPTION_CHOICES = [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')]

    video          = models.ForeignKey('videos.Video', on_delete=models.CASCADE, related_name='quiz_drafts')
    question_text  = models.TextField()
    option_a       = models.CharField(max_length=500)
    option_b       = models.CharField(max_length=500)
    option_c       = models.CharField(max_length=500)
    option_d       = models.CharField(max_length=500)
    correct_option = models.CharField(max_length=1, choices=OPTION_CHOICES)
    explanation    = models.TextField(blank=True)
    status         = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    admin_note     = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['video', 'created_at']

    def __str__(self):
        return f"Draft [{self.status}] {self.question_text[:60]}"


class Quiz(models.Model):
    video        = models.ForeignKey('videos.Video', on_delete=models.CASCADE, related_name='quizzes')
    title        = models.CharField(max_length=255)
    is_published = models.BooleanField(default=False, db_index=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Quizzes'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def question_count(self):
        return self.questions.count()


class QuizQuestion(models.Model):
    OPTION_CHOICES = [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')]

    quiz           = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    question_text  = models.TextField()
    option_a       = models.CharField(max_length=500)
    option_b       = models.CharField(max_length=500)
    option_c       = models.CharField(max_length=500)
    option_d       = models.CharField(max_length=500)
    correct_option = models.CharField(max_length=1, choices=OPTION_CHOICES)
    explanation    = models.TextField(blank=True)
    order          = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Q{self.order}: {self.question_text[:60]}"

    def get_option_text(self, letter):
        return getattr(self, f'option_{letter}', '')


class StudentQuizAttempt(models.Model):
    student         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quiz_attempts')
    quiz            = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    score           = models.PositiveIntegerField(default=0)
    total_questions = models.PositiveIntegerField(default=0)
    completed_at    = models.DateTimeField(auto_now_add=True)
    answers         = models.JSONField(default=dict)

    class Meta:
        unique_together = ('student', 'quiz')

    def __str__(self):
        return f"{self.student.username} - {self.quiz.title} ({self.score}/{self.total_questions})"

    @property
    def percentage(self):
        if self.total_questions == 0:
            return 0
        return round(self.score / self.total_questions * 100)

    @property
    def passed(self):
        return self.percentage >= 60
