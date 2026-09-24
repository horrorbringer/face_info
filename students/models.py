from django.conf import settings
from django.db import models


class Student(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="student_profile")
    student_id = models.CharField(max_length=64, unique=True)
    full_name = models.CharField(max_length=255)
    class_room = models.ForeignKey("attendance.ClassRoom", on_delete=models.SET_NULL, null=True, blank=True, related_name="primary_students")
    classrooms = models.ManyToManyField("attendance.ClassRoom", blank=True, related_name="students")
    guardian_contact = models.CharField(max_length=100, blank=True)
    class_year = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    consent_given_at = models.DateTimeField(null=True, blank=True)
    consent_reference = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "student_id"]
        indexes = [
            models.Index(fields=["class_room", "is_active"]),
        ]

    def __str__(self):
        return f"{self.student_id} — {self.full_name}"

    def get_enrolled_classrooms(self):
        """Returns all classrooms the student belongs to (both ManyToMany and primary)."""
        from attendance.models import ClassRoom
        if self.class_room_id:
            return ClassRoom.objects.filter(models.Q(students=self) | models.Q(id=self.class_room_id)).distinct()
        return self.classrooms.all()

    def is_enrolled_in(self, classroom):
        """Checks if student is enrolled in the given classroom."""
        if not classroom:
            return False
        c_id = classroom.id if hasattr(classroom, "id") else classroom
        if self.class_room_id == c_id:
            return True
        return self.classrooms.filter(id=c_id).exists()

    @property
    def guardian_email(self):
        """Returns valid email address from guardian contact, or None."""
        c = (self.guardian_contact or "").strip()
        return c if "@" in c else None

    @property
    def guardian_telegram_id(self):
        """Returns numeric Telegram chat_id from guardian contact, or None."""
        c = (self.guardian_contact or "").strip()
        return c if c.lstrip("-").isdigit() else None

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

