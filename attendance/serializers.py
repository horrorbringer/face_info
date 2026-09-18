from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from students.models import Student
from .models import AttendanceRecord, ClassRoom, Session, StudentFace, Teacher


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        username = data.get("username")
        password = data.get("password")
        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")
        data["user"] = user
        return data


class TeacherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = ["id", "name", "email"]


class ClassRoomSerializer(serializers.ModelSerializer):
    teacher = TeacherSerializer(read_only=True)

    class Meta:
        model = ClassRoom
        fields = ["id", "name", "teacher"]


class StudentProfileSerializer(serializers.ModelSerializer):
    class_room = ClassRoomSerializer(read_only=True)
    face_embeddings_count = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            "id", "student_id", "full_name", "name", "class_year",
            "is_active", "guardian_contact", "class_room", "face_embeddings_count",
            "consent_given_at"
        ]

    def get_face_embeddings_count(self, obj):
        return obj.face_embeddings.count()


class SessionSerializer(serializers.ModelSerializer):
    class_room = ClassRoomSerializer(read_only=True)
    is_ended = serializers.SerializerMethodField()
    is_qr_valid = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = [
            "id", "class_room", "date", "start_time", "end_time",
            "qr_token", "qr_token_expires_at", "ended_at", "is_ended", "is_qr_valid"
        ]

    def get_is_ended(self, obj):
        return obj.ended_at is not None

    def get_is_qr_valid(self, obj):
        if not obj.qr_token or not obj.qr_token_expires_at:
            return False
        from django.utils import timezone
        return timezone.now() < obj.qr_token_expires_at


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student_id = serializers.CharField(source="student.student_id", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    class_room_name = serializers.CharField(source="session.class_room.name", read_only=True)
    edited_by_username = serializers.CharField(source="edited_by.username", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = [
            "id", "student", "student_id", "student_name", "session", "class_room_name",
            "status", "method", "checked_in_at", "confidence_score", "is_deleted",
            "edited_by_username"
        ]
        read_only_fields = ["checked_in_at", "confidence_score", "edited_by_username"]


class QRCheckInSerializer(serializers.Serializer):
    qr_token = serializers.CharField(max_length=128)


class FaceCheckInSerializer(serializers.Serializer):
    image = serializers.ImageField()
    session_id = serializers.IntegerField(required=False)


class AttendanceOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ["status", "is_deleted"]
