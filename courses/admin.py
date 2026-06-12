from django.contrib import admin

from .models import Course, Enrollment


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'instructor', 'language', 'is_active', 'created_at')
    list_filter = ('language', 'is_active')
    search_fields = ('title', 'description', 'instructor__username')
    raw_id_fields = ('instructor',)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'enrolled_at', 'is_active')
    list_filter = ('is_active', 'course')
    search_fields = ('student__username', 'course__title')
    raw_id_fields = ('student', 'course')
