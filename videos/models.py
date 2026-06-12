from django.db import models


class Video(models.Model):
    class Status(models.TextChoices):
        UPLOADED = 'UPLOADED', 'Awaiting Transcript'
        PROCESSING = 'PROCESSING', 'Processing'
        READY = 'READY', 'Ready'
        FAILED = 'FAILED', 'Failed'

    class Language(models.TextChoices):
        ENGLISH = 'en', 'English'
        MALAYALAM = 'ml', 'Malayalam'
        MIXED = 'mixed', 'Mixed'

    course = models.ForeignKey(
        'courses.Course', on_delete=models.CASCADE, related_name='videos'
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    video_key = models.CharField(max_length=500, blank=True)
    audio_key = models.CharField(max_length=500, blank=True)
    language_code = models.CharField(
        max_length=10, choices=Language.choices, default=Language.ENGLISH
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UPLOADED
    )
    english_transcript = models.TextField(blank=True)
    malayalam_transcript = models.TextField(blank=True)
    detected_language = models.CharField(max_length=10, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    english_pdf_key = models.CharField(max_length=500, blank=True)
    malayalam_pdf_key = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} [{self.status}]'

    @property
    def status_badge_class(self):
        return {
            self.Status.UPLOADED: 'secondary',
            self.Status.PROCESSING: 'warning',
            self.Status.READY: 'success',
            self.Status.FAILED: 'danger',
        }.get(self.status, 'secondary')


class TranscriptChunk(models.Model):
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name='chunks')
    chunk_index = models.IntegerField()
    text = models.TextField(blank=True)
    start_time = models.FloatField()
    end_time = models.FloatField()
    is_processed = models.BooleanField(default=False)
    embedding_id = models.CharField(max_length=200, blank=True)
    topic_segment = models.IntegerField(default=0)

    class Meta:
        ordering = ['chunk_index']
        unique_together = ('video', 'chunk_index')

    def __str__(self):
        return f'Chunk {self.chunk_index} [{self.start_time:.1f}s-{self.end_time:.1f}s]'
