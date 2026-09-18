from django.conf import settings
from django.db import models


class Student(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="student_profile")
    student_id = models.CharField(max_length=64, unique=True)
    full_name = models.CharField(max_length=255)
    class_room = models.ForeignKey("attendance.ClassRoom", on_delete=models.SET_NULL, null=True, blank=True, related_name="students")
    guardian_contact = models.CharField(max_length=100, blank=True)
    class_year = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    consent_given_at = models.DateTimeField(null=True, blank=True)
    consent_reference = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "student_id"]

    def __str__(self):
        return f"{self.student_id} — {self.full_name}"

    @property
    def name(self):
        return self.full_name

    @name.setter
    def name(self, val):
        self.full_name = val

    @property
    def can_be_matched(self):
        return self.is_active and self.consent_given_at is not None


class FaceEmbedding(models.Model):
    """Biometric template only. Never add a photo field to this model."""
    student = models.OneToOneField(Student, on_delete=models.CASCADE, related_name="face_embedding")
    vector = models.JSONField()
    model_name = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

