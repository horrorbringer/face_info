from django.conf import settings
from django.db import models


class Teacher(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="teacher_profile")
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)

    def save(self, *args, **kwargs):
        if self.user:
            if not self.email and self.user.email:
                self.email = self.user.email
            if not self.name:
                self.name = self.user.get_full_name() or self.user.username
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.email})"


class ClassRoom(models.Model):
    name = models.CharField(max_length=100)
    room = models.CharField(max_length=50, blank=True, default="", help_text="Physical room/lab identifier, e.g. Room 204 or Lab B")
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True, related_name="classrooms")
    co_teachers = models.ManyToManyField(Teacher, blank=True, related_name="assistant_classrooms", help_text="Assistant or co-teachers authorized to manage this classroom.")

    def __str__(self):
        if self.room:
            return f"{self.name} ({self.room})"
        return self.name

    def get_enrolled_students(self, active_only=True):
        """Returns all students enrolled in this class (via ManyToMany classrooms or primary class_room)."""
        from students.models import Student
        from django.db.models import Q
        qs = Student.objects.filter(Q(classrooms=self) | Q(class_room=self)).distinct()
        if active_only:
            qs = qs.filter(is_active=True)
        return qs


class Session(models.Model):
    class_room = models.ForeignKey(ClassRoom, on_delete=models.CASCADE, related_name="sessions")
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    started_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Timestamp when session was activated/started.")
    qr_token = models.CharField(max_length=128, unique=True, null=True, blank=True)
    qr_token_expires_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_cancelled = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["class_room", "date"]),
            models.Index(fields=["date", "ended_at"]),
            models.Index(fields=["is_cancelled", "date"]),
        ]

    @property
    def is_cancelled_or_inactive(self):
        return self.is_cancelled or self.qr_token == "CANCELLED"

    def __str__(self):
        return f"{self.class_room.name} - {self.date} ({self.start_time} - {self.end_time})"


class AttendanceRecord(models.Model):
    STATUS_CHOICES = [("present", "Present"), ("late", "Late"), ("absent", "Absent")]
    METHOD_CHOICES = [
        ("qr", "QR Code"),
        ("face", "Face Recognition"),
        ("manual", "Manual"),
        ("system", "System / Auto"),
    ]

    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="attendance_records")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="attendance_records")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="manual")
    checked_in_at = models.DateTimeField(null=True, blank=True, db_index=True)
    confidence_score = models.FloatField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ("student", "session")
        indexes = [
            models.Index(fields=["session", "is_deleted"]),
            models.Index(fields=["session", "status", "is_deleted"]),
            models.Index(fields=["student", "checked_in_at"]),
        ]

    def save(self, *args, **kwargs):
        from django.utils import timezone
        if self.status in ("present", "late") and not self.checked_in_at:
            self.checked_in_at = timezone.now()
        elif self.status == "absent" and "checked_in_at" not in kwargs.get("update_fields", []):
            self.checked_in_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} - {self.session}: {self.status}"


class StudentFace(models.Model):
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="face_embeddings")
    embedding = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Embedding for {self.student} ({self.created_at})"


class AlertLog(models.Model):
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="alert_logs")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="alert_logs")
    channel = models.CharField(max_length=20)
    status = models.CharField(max_length=20, db_index=True)
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    error_message = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["student", "session", "status"]),
        ]

    def __str__(self):
        return f"Alert {self.channel} for {self.student} - {self.status}"
