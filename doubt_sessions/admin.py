from django.contrib import admin
from django.utils import timezone

from .models import DoubtSession, InstructorSlot


@admin.register(InstructorSlot)
class InstructorSlotAdmin(admin.ModelAdmin):
    list_display  = ('id', 'instructor', 'slot_local', 'is_available')
    list_filter   = ('is_available', 'instructor')
    ordering      = ('slot_datetime',)

    def slot_local(self, obj):
        return timezone.localtime(obj.slot_datetime).strftime('%Y-%m-%d %H:%M')
    slot_local.short_description = 'Slot (local)'


@admin.register(DoubtSession)
class DoubtSessionAdmin(admin.ModelAdmin):
    list_display    = ('id', 'student', 'instructor', 'slot_local', 'status', 'created_at')
    list_filter     = ('status',)
    readonly_fields = ('created_at',)
    ordering        = ('-created_at',)

    def slot_local(self, obj):
        return timezone.localtime(obj.slot.slot_datetime).strftime('%Y-%m-%d %H:%M')
    slot_local.short_description = 'Slot (local)'
