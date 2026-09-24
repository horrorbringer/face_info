from django.contrib import admin
from .models import AlertLog, AttendanceRecord, ClassRoom, Session, StudentFace, Teacher


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "user")
    search_fields = ("name", "email")


@admin.register(ClassRoom)
class ClassRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "room", "teacher", "get_co_teachers_display")
    search_fields = ("name", "room")
    list_filter = ("teacher",)
    filter_horizontal = ("co_teachers",)

    @admin.display(description="Co-Teachers")
    def get_co_teachers_display(self, obj):
        names = [t.name for t in obj.co_teachers.all()]
        return ", ".join(names) if names else "None"


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("class_room", "date", "start_time", "end_time", "started_at", "ended_at", "is_cancelled")
    list_filter = ("is_cancelled", "class_room", "date")
    search_fields = ("class_room__name", "qr_token")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "status", "method", "checked_in_at", "confidence_score", "is_deleted", "edited_by")
    list_filter = ("status", "method", "is_deleted", "session__date", "session__class_room")
    search_fields = ("student__full_name", "student__student_id", "session__class_room__name")
    actions = ["soft_delete", "restore"]

    @admin.action(description="Soft delete selected records")
    def soft_delete(self, request, queryset):
        queryset.update(is_deleted=True, edited_by=request.user)

    @admin.action(description="Restore selected records")
    def restore(self, request, queryset):
        queryset.update(is_deleted=False, edited_by=request.user)


@admin.register(StudentFace)
class StudentFaceAdmin(admin.ModelAdmin):
    list_display = ("student", "created_at")
    search_fields = ("student__full_name", "student__student_id")


@admin.register(AlertLog)
class AlertLogAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "channel", "status", "sent_at")
    list_filter = ("channel", "status", "sent_at")
    search_fields = ("student__full_name", "student__student_id")
