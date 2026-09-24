from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from students.models import Student
from .models import AlertLog, AttendanceRecord, ClassRoom, Session, StudentFace, Teacher


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
    classrooms = serializers.SerializerMethodField()
    face_embeddings_count = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            "id", "student_id", "full_name", "name", "class_year",
            "is_active", "guardian_contact", "class_room", "classrooms", "face_embeddings_count",
            "consent_given_at"
        ]

    def get_classrooms(self, obj):
        classes = obj.get_enrolled_classrooms()
        return ClassRoomSerializer(classes, many=True).data

    def get_face_embeddings_count(self, obj):
        return obj.face_embeddings.count()


class SessionSerializer(serializers.ModelSerializer):
    class_room = ClassRoomSerializer(read_only=True)
    is_ended = serializers.SerializerMethodField()
    is_qr_valid = serializers.SerializerMethodField()
    is_cancelled = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = [
            "id", "class_room", "date", "start_time", "end_time",
            "qr_token", "qr_token_expires_at", "ended_at", "is_ended", "is_qr_valid", "is_cancelled"
        ]

    def get_is_ended(self, obj):
        return obj.ended_at is not None

    def get_is_cancelled(self, obj):
        return obj.qr_token == "CANCELLED"

    def get_is_qr_valid(self, obj):
        if not obj.qr_token or not obj.qr_token_expires_at or obj.qr_token == "CANCELLED":
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


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "New passwords do not match."})
        user = self.context.get("request").user
        if not user.check_password(data["old_password"]):
            raise serializers.ValidationError({"old_password": "Old password is incorrect."})
        return data


class SessionCreateSerializer(serializers.ModelSerializer):
    auto_generate_qr = serializers.BooleanField(default=True, write_only=True, required=False)
    qr_expiry_minutes = serializers.IntegerField(default=120, write_only=True, required=False)

    class Meta:
        model = Session
        fields = [
            "id", "class_room", "date", "start_time", "end_time",
            "auto_generate_qr", "qr_expiry_minutes"
        ]

    def create(self, validated_data):
        import datetime
        import secrets
        from django.utils import timezone

        auto_qr = validated_data.pop("auto_generate_qr", True)
        expiry_minutes = validated_data.pop("qr_expiry_minutes", 120)

        if auto_qr:
            validated_data["qr_token"] = secrets.token_urlsafe(32)
            validated_data["qr_token_expires_at"] = timezone.now() + datetime.timedelta(minutes=expiry_minutes)

        return super().create(validated_data)


class SessionRosterItemSerializer(serializers.Serializer):
    student_pk = serializers.IntegerField(source="id")
    student_id = serializers.CharField()
    student_name = serializers.CharField(source="full_name")
    is_active = serializers.BooleanField()
    guardian_contact = serializers.CharField()
    attendance_status = serializers.CharField()
    method = serializers.CharField(allow_null=True)
    checked_in_at = serializers.DateTimeField(allow_null=True)
    confidence_score = serializers.FloatField(allow_null=True)
    record_id = serializers.IntegerField(allow_null=True)
    is_deleted = serializers.BooleanField(default=False)


class BulkAttendanceItemSerializer(serializers.Serializer):
    student_id = serializers.CharField(required=False)
    student_pk = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(choices=AttendanceRecord.STATUS_CHOICES)


class BulkAttendanceOverrideSerializer(serializers.Serializer):
    records = BulkAttendanceItemSerializer(many=True)


class AlertLogSerializer(serializers.ModelSerializer):
    session_name = serializers.CharField(source="session.class_room.name", read_only=True)
    session_date = serializers.DateField(source="session.date", read_only=True)

    class Meta:
        model = AlertLog
        fields = ["id", "session", "session_name", "session_date", "channel", "status", "sent_at", "error_message"]


class StudentSessionScheduleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    class_room = serializers.CharField(source="class_room.name")
    date = serializers.DateField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    is_qr_active = serializers.BooleanField()
    is_checked_in = serializers.BooleanField()
    is_cancelled = serializers.BooleanField(default=False)
    my_status = serializers.CharField(allow_null=True)
    my_method = serializers.CharField(allow_null=True)
    checked_in_at = serializers.DateTimeField(allow_null=True)

