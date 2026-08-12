from django.contrib import admin
from .models import FaceEmbedding, Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "full_name", "class_year", "is_active", "consent_given_at")
    search_fields = ("student_id", "full_name")
    list_filter = ("is_active", "class_year")


@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("student", "model_name", "updated_at")
    readonly_fields = ("created_at", "updated_at")

