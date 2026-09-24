from django.contrib import admin
from .models import FaceEmbedding, Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "full_name", "get_classes_display", "guardian_contact", "is_active", "consent_given_at")
    search_fields = ("student_id", "full_name", "guardian_contact")
    list_filter = ("is_active", "classrooms", "class_year")
    filter_horizontal = ("classrooms",)

    @admin.display(description="Enrolled Classes")
    def get_classes_display(self, obj):
        classes = obj.get_enrolled_classrooms()
        names = [c.name for c in classes]
        return ", ".join(names) if names else "None"



@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("student", "model_name", "updated_at")
    readonly_fields = ("created_at", "updated_at")

