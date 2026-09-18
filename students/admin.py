from django.contrib import admin
from .models import FaceEmbedding, Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "full_name", "class_room", "guardian_contact", "is_active", "consent_given_at")
    search_fields = ("student_id", "full_name", "guardian_contact")
    list_filter = ("is_active", "class_room", "class_year")



@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("student", "model_name", "updated_at")
    readonly_fields = ("created_at", "updated_at")

