from django.contrib import admin

from .models import TranscriptChunk, Video


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'status', 'language_code', 'duration_seconds', 'created_at')
    list_filter = ('status', 'language_code', 'course')
    search_fields = ('title', 'course__title')
    readonly_fields = ('video_key', 'audio_key', 'detected_language', 'created_at')


@admin.register(TranscriptChunk)
class TranscriptChunkAdmin(admin.ModelAdmin):
    list_display = ('video', 'chunk_index', 'start_time', 'end_time', 'is_processed', 'embedding_id')
    list_filter = ('is_processed', 'video__course')
    search_fields = ('video__title', 'text')
