from django.contrib import admin

from .models import Quiz, QuizDraft, QuizQuestion, StudentQuizAttempt


@admin.register(QuizDraft)
class QuizDraftAdmin(admin.ModelAdmin):
    list_display  = ('id', 'video', 'question_text_short', 'correct_option', 'status', 'created_at')
    list_filter   = ('status', 'video__course')
    search_fields = ('question_text', 'video__title')
    ordering      = ('-created_at',)

    def question_text_short(self, obj):
        return obj.question_text[:60]
    question_text_short.short_description = 'Question'


class QuizQuestionInline(admin.TabularInline):
    model  = QuizQuestion
    extra  = 0
    fields = ('order', 'question_text', 'correct_option', 'explanation')


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display  = ('id', 'title', 'video', 'is_published', 'question_count', 'created_at')
    list_filter   = ('is_published', 'video__course')
    search_fields = ('title', 'video__title')
    inlines       = [QuizQuestionInline]
    ordering      = ('-created_at',)

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = 'Questions'


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display  = ('id', 'quiz', 'order', 'question_text_short', 'correct_option')
    list_filter   = ('quiz__video__course',)
    search_fields = ('question_text', 'quiz__title')
    ordering      = ('quiz', 'order')

    def question_text_short(self, obj):
        return obj.question_text[:60]
    question_text_short.short_description = 'Question'


@admin.register(StudentQuizAttempt)
class StudentQuizAttemptAdmin(admin.ModelAdmin):
    list_display  = ('id', 'student', 'quiz', 'score', 'total_questions', 'pct', 'completed_at')
    list_filter   = ('quiz__video__course',)
    search_fields = ('student__username', 'quiz__title')
    readonly_fields = ('answers',)

    def pct(self, obj):
        return f'{obj.percentage}%'
    pct.short_description = '%'
