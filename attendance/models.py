from django.conf import settings
from django.db import models


class Teacher(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="teacher_profile")
    name = models.CharField(max_length=255)
    email = models.EmailField()

    def __str__(self):
        return f"{self.name} ({self.email})"


class ClassRoom(models.Model):
    name = models.CharField(max_length=100)
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True, related_name="classrooms")

    def __str__(self):
        return self.name


class Session(models.Model):
    class_room = models.ForeignKey(ClassRoom, on_delete=models.CASCADE, related_name="sessions")
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    qr_token = models.CharField(max_length=128, unique=True, null=True, blank=True)
    qr_token_expires_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["class_room", "date"]),
            models.Index(fields=["date", "ended_at"]),
        ]

    def __str__(self):
        return f"{self.class_room.name} - {self.date} ({self.start_time} - {self.end_time})"


class AttendanceRecord(models.Model):
    STATUS_CHOICES = [("present", "Present"), ("late", "Late"), ("absent", "Absent")]
    METHOD_CHOICES = [("qr", "QR Code"), ("face", "Face Recognition"), ("manual", "Manual")]

    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="attendance_records")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="attendance_records")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    checked_in_at = models.DateTimeField(auto_now_add=True, db_index=True)
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
