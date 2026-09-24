import datetime
import logging
import secrets
import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from students.models import Student
from .models import AlertLog, AttendanceRecord, ClassRoom, Session, StudentFace, Teacher
from .security import (
    get_dynamic_qr_info,
    is_client_ip_allowed,
    verify_dynamic_qr_token,
)
from .serializers import (
    AlertLogSerializer,
    AttendanceOverrideSerializer,
    AttendanceRecordSerializer,
    BulkAttendanceOverrideSerializer,
    ChangePasswordSerializer,
    ClassRoomSerializer,
    LoginSerializer,
    QRCheckInSerializer,
    SessionCreateSerializer,
    SessionRosterItemSerializer,
    SessionSerializer,
    StudentProfileSerializer,
    StudentSessionScheduleSerializer,
)
from .tasks import send_absence_alerts_for_session

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Authentication & Profile
# ----------------------------------------------------------------------

class LoginView(APIView):
    """
    POST /api/auth/login/
    Token authentication for both students and teachers.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)

        role = "admin" if user.is_staff else "user"
        student_data = None
        teacher_data = None

        if hasattr(user, "student_profile"):
            role = "student"
            student_data = {
                "id": user.student_profile.id,
                "student_id": user.student_profile.student_id,
                "name": user.student_profile.full_name,
            }
        elif hasattr(user, "teacher_profile"):
            role = "teacher"
            teacher_data = {
                "id": user.teacher_profile.id,
                "name": user.teacher_profile.name,
                "email": user.teacher_profile.email,
            }

        return Response({
            "token": token.key,
            "user_id": user.id,
            "username": user.username,
            "role": role,
            "student": student_data,
            "teacher": teacher_data,
        })


class LogoutView(APIView):
    """
    POST /api/auth/logout/
    Revokes the current user's DRF token.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response({"message": "Successfully logged out. Token revoked."}, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """
    POST /api/auth/change-password/
    Allows authenticated users to change their password.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response({
            "message": "Password changed successfully.",
            "token": token.key,
        }, status=status.HTTP_200_OK)


class StudentMeView(APIView):
    """
    GET /api/students/me/
    Returns the profile and classroom info for the authenticated student.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not hasattr(request.user, "student_profile"):
            return Response({"error": "No student profile associated with this account."}, status=status.HTTP_404_NOT_FOUND)
        student = request.user.student_profile
        serializer = StudentProfileSerializer(student)
        return Response(serializer.data)


class StudentTodayScheduleView(APIView):
    """
    GET /api/students/schedule/today/
    Returns today's classroom sessions for the authenticated student,
    including check-in status and active QR indicator for Home Dashboard.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not hasattr(request.user, "student_profile"):
            return Response({"error": "No student profile found."}, status=status.HTTP_404_NOT_FOUND)
        student = request.user.student_profile
        enrolled_classrooms = student.get_enrolled_classrooms()
        if not enrolled_classrooms.exists():
            return Response([])

        today = timezone.localdate()
        sessions = list(Session.objects.filter(class_room__in=enrolled_classrooms, date=today).select_related("class_room").order_by("start_time"))

        from .tasks import sync_sessions_lifecycle
        sync_sessions_lifecycle(sessions)

        records = AttendanceRecord.objects.filter(student=student, session__in=sessions, is_deleted=False)
        record_map = {r.session_id: r for r in records}

        now = timezone.now()
        schedule_data = []
        for s in sessions:
            rec = record_map.get(s.id)
            is_cancelled = s.is_cancelled_or_inactive
            is_qr_active = bool(
                s.qr_token
                and s.qr_token_expires_at
                and now < s.qr_token_expires_at
                and not s.ended_at
                and not is_cancelled
            )
            is_checked_in = rec is not None and rec.status in ("present", "late")
            schedule_data.append({
                "id": s.id,
                "class_room": s.class_room,
                "date": s.date,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "is_qr_active": is_qr_active,
                "is_checked_in": is_checked_in,
                "is_cancelled": is_cancelled,
                "my_status": rec.status if rec else None,
                "my_method": rec.method if rec else None,
                "checked_in_at": rec.checked_in_at if rec else None,
            })

        serializer = StudentSessionScheduleSerializer(schedule_data, many=True)
        return Response(serializer.data)



class StudentAlertsMineView(APIView):
    """
    GET /api/alerts/mine/
    Returns in-app absence alerts / notification history for the authenticated student.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not hasattr(request.user, "student_profile"):
            return Response({"error": "No student profile found."}, status=status.HTTP_404_NOT_FOUND)
        student = request.user.student_profile
        alerts = AlertLog.objects.filter(student=student).select_related("session", "session__class_room").order_by("-sent_at")[:50]
        serializer = AlertLogSerializer(alerts, many=True)
        return Response(serializer.data)


# ----------------------------------------------------------------------
# QR Check-in & Student History (Phase 2)
# ----------------------------------------------------------------------

class QRCheckInView(APIView):
    """
    POST /api/attendance/checkin/qr/
    Body: { "qr_token": "..." }
    Validates token, checks expiry, and records student attendance as present or late.
    Supports both dynamic rotating tokens (dyn_<id>_<hash>) and standard tokens.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "attendance_checkin"

    def post(self, request):
        if not is_client_ip_allowed(request):
            return Response(
                {"error": "Attendance check-in is restricted to the authorized campus network."},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = QRCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        qr_token = serializer.validated_data["qr_token"].strip()

        if not hasattr(request.user, "student_profile"):
            return Response({"error": "Only registered students can check in via QR code.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)
        student = request.user.student_profile
        if not student.is_active:
            return Response({"error": "Student account is suspended or inactive. Check-in is not permitted.", "code": "STUDENT_INACTIVE"}, status=status.HTTP_403_FORBIDDEN)


        session = None
        if qr_token.startswith("dyn_"):
            parts = qr_token.split("_")
            if len(parts) >= 3 and parts[1].isdigit():
                sess_id = int(parts[1])
                try:
                    session = Session.objects.select_related("class_room").get(id=sess_id)
                except Session.DoesNotExist:
                    session = None

            if not session or not verify_dynamic_qr_token(session.id, qr_token):
                return Response(
                    {
                        "error": "Dynamic QR code has expired or is invalid. Please scan the current code on screen.",
                        "code": "QR_EXPIRED",
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            try:
                session = Session.objects.select_related("class_room").get(qr_token=qr_token)
            except Session.DoesNotExist:
                return Response({
                    "error": "Invalid QR code.",
                    "code": "INVALID_QR"
                }, status=status.HTTP_400_BAD_REQUEST)

            now = timezone.now()
            if not session.qr_token_expires_at or now > session.qr_token_expires_at:
                return Response({
                    "error": "QR code has expired. Ask your teacher for a refreshed code.",
                    "code": "QR_EXPIRED"
                }, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        if session.ended_at:
            return Response({
                "error": "This session has already ended.",
                "code": "SESSION_ENDED"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Guard against Zombie sessions (time travel / unended sessions after class duration)
        session_start_datetime = timezone.make_aware(
            datetime.datetime.combine(session.date, session.start_time),
            timezone.get_current_timezone()
        )
        max_duration_hours = getattr(settings, "SESSION_MAX_DURATION_HOURS", 4)
        session_cutoff = session_start_datetime + datetime.timedelta(hours=max_duration_hours)
        if session.date < now.date() or now > session_cutoff:
            if not session.ended_at:
                session.ended_at = now
                session.save(update_fields=["ended_at"])
            return Response({
                "error": "This session has expired. Check-in is no longer allowed.",
                "code": "SESSION_EXPIRED"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Enforce student enrollment in this session's class (supports multi-class enrollment)
        if not student.is_enrolled_in(session.class_room):
            return Response({
                "error": f"You are not enrolled in {session.class_room.name}.",
                "code": "NOT_ENROLLED"
            }, status=status.HTTP_403_FORBIDDEN)

        # Guard against Bilocation / Impossible Travel (checking in to 2 different classes within 15 mins)
        fifteen_mins_ago = now - datetime.timedelta(minutes=15)
        conflicting_checkin = AttendanceRecord.objects.filter(
            student=student,
            status__in=["present", "late"],
            checked_in_at__gte=fifteen_mins_ago,
            is_deleted=False
        ).exclude(session=session).select_related("session__class_room").first()

        if conflicting_checkin:
            return Response({
                "error": f"Impossible check-in: You already checked in to {conflicting_checkin.session.class_room.name} less than 15 minutes ago.",
                "code": "IMPOSSIBLE_TRAVEL"
            }, status=status.HTTP_409_CONFLICT)

        # Guard against Device Hopping (Buddy Punching on a single phone)
        device_id = request.data.get("device_id")
        if device_id:
            device_cache_key = f"session_device:{session.id}:{device_id}"
            bound_student_id = cache.get(device_cache_key)
            if bound_student_id and bound_student_id != student.id:
                return Response({
                    "error": "This physical device has already been used to check in another student for this session.",
                    "code": "DEVICE_REUSE_BLOCKED"
                }, status=status.HTTP_409_CONFLICT)
            cache.set(device_cache_key, student.id, timeout=43200)

        # Calculate late vs present based on session actual started_at (or scheduled start_time) + threshold
        late_threshold_mins = getattr(settings, "LATE_THRESHOLD_MINUTES", 15)
        effective_start = session.started_at or session_start_datetime
        late_cutoff = effective_start + datetime.timedelta(minutes=late_threshold_mins)
        record_status = "late" if now > late_cutoff else "present"

        try:
            with transaction.atomic():
                record, created = AttendanceRecord.objects.get_or_create(
                    student=student,
                    session=session,
                    defaults={
                        "status": record_status,
                        "method": "qr",
                        "checked_in_at": now,
                        "is_deleted": False,
                    }
                )
        except IntegrityError:
            record = AttendanceRecord.objects.get(student=student, session=session)
            created = False

        if not created:
            if record.is_deleted:
                record.is_deleted = False
                record.status = record_status
                record.method = "qr"
                record.checked_in_at = now
                record.save()
            elif record.edited_by is not None:
                # Teacher Manual Override is Sovereign: automated check-in cannot silently override teacher's mark
                return Response({
                    "error": f"Attendance was manually marked by {record.edited_by.username} as '{record.status}'. Automated check-in cannot override teacher's record.",
                    "code": "TEACHER_LOCKED",
                    "status": record.status,
                    "record": AttendanceRecordSerializer(record).data,
                }, status=status.HTTP_409_CONFLICT)
            elif record.status == "absent":
                # Upgrade from automated absent mark to late/present upon late arrival or session reopen
                record.status = record_status
                record.method = "qr"
                record.checked_in_at = now
                record.save()
            else:
                return Response({
                    "message": f"Already checked in as '{record.status}'.",
                    "code": "ALREADY_CHECKED_IN",
                    "record": AttendanceRecordSerializer(record).data,
                }, status=status.HTTP_200_OK)

        return Response({
            "message": f"Successfully checked in ({record_status}).",
            "record": AttendanceRecordSerializer(record).data,
        }, status=status.HTTP_201_CREATED)


class StudentAttendanceHistoryView(APIView):
    """
    GET /api/attendance/history/
    Returns non-deleted attendance records for the authenticated student.
    Query parameters supported:
      - status: 'present', 'late', 'absent'
      - date_from: YYYY-MM-DD
      - date_to: YYYY-MM-DD
      - limit: int (max: 200)
      - offset: int
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not hasattr(request.user, "student_profile"):
            return Response({"error": "No student profile found."}, status=status.HTTP_404_NOT_FOUND)
        student = request.user.student_profile
        records = (
            AttendanceRecord.objects.filter(student=student, is_deleted=False, session__is_cancelled=False)
            .exclude(session__qr_token="CANCELLED")
            .select_related("session", "session__class_room")
            .order_by("-session__date", "-session__start_time")
        )

        status_param = request.query_params.get("status")
        if status_param:
            records = records.filter(status=status_param.lower())

        date_from = request.query_params.get("date_from")
        if date_from:
            records = records.filter(session__date__gte=date_from)

        date_to = request.query_params.get("date_to")
        if date_to:
            records = records.filter(session__date__lte=date_to)

        limit_param = request.query_params.get("limit")
        offset_param = request.query_params.get("offset")
        if limit_param or offset_param:
            try:
                limit = min(int(limit_param or 50), 200)
            except ValueError:
                limit = 50
            try:
                offset = max(int(offset_param or 0), 0)
            except ValueError:
                offset = 0
            records = records[offset : offset + limit]

        serializer = AttendanceRecordSerializer(records, many=True)
        return Response(serializer.data)


# ----------------------------------------------------------------------
# Teacher Session Management & Overrides (Phases 2, 3, 6)
# ----------------------------------------------------------------------

def is_authorized_teacher_or_staff(user, classroom=None):
    """
    Validates that the authenticated user is either a Django staff/admin
    or an assigned primary or co-Teacher. If classroom is provided, verifies ownership.
    Explicitly rejects students and non-assigned teachers.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if hasattr(user, "teacher_profile"):
        if classroom is None:
            return True
        t_id = user.teacher_profile.id
        if classroom.teacher_id == t_id:
            return True
        return classroom.co_teachers.filter(id=t_id).exists()
    return False


class TeacherTodayClassesView(APIView):
    """
    GET /api/teacher/classes/today/
    Returns all sessions for today for the authenticated teacher (primary or co-teacher).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_authorized_teacher_or_staff(request.user):
            return Response(
                {"error": "Only teachers and staff can access the teaching schedule.", "code": "UNAUTHORIZED"},
                status=status.HTTP_403_FORBIDDEN
            )

        today = timezone.localdate()
        sessions_qs = Session.objects.filter(date=today, is_cancelled=False).select_related("class_room")
        if not request.user.is_staff:
            t = request.user.teacher_profile
            sessions_qs = sessions_qs.filter(Q(class_room__teacher=t) | Q(class_room__co_teachers=t)).distinct()
        sessions = list(sessions_qs)

        from .tasks import sync_sessions_lifecycle
        sync_sessions_lifecycle(sessions)

        serializer = SessionSerializer(sessions, many=True)
        return Response(serializer.data)


class TeacherClassRoomListCreateView(APIView):
    """
    GET /api/teacher/classrooms/
    Returns all classrooms assigned to the authenticated teacher (or all for staff).

    POST /api/teacher/classrooms/
    Creates a new classroom assigned to the authenticated teacher.
    Payload: {"name": "Machine Learning Lab"}
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_authorized_teacher_or_staff(request.user):
            return Response(
                {"error": "Only teachers and staff can access classrooms.", "code": "UNAUTHORIZED"},
                status=status.HTTP_403_FORBIDDEN
            )

        teacher_profile = getattr(request.user, "teacher_profile", None)
        if teacher_profile and request.query_params.get("all") != "true":
            classrooms = ClassRoom.objects.filter(
                Q(teacher=teacher_profile) | Q(co_teachers=teacher_profile)
            ).distinct().select_related("teacher").prefetch_related("co_teachers").order_by("name")
        else:
            classrooms = ClassRoom.objects.all().select_related("teacher").prefetch_related("co_teachers").order_by("name")

        serializer = ClassRoomSerializer(classrooms, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if not is_authorized_teacher_or_staff(request.user):
            return Response(
                {"error": "Only teachers and staff can create classrooms.", "code": "UNAUTHORIZED"},
                status=status.HTTP_403_FORBIDDEN
            )

        name = request.data.get("name", "").strip()
        if not name:
            return Response(
                {"error": "Class name is required.", "code": "INVALID_NAME"},
                status=status.HTTP_400_BAD_REQUEST
            )

        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile and not request.user.is_staff:
            return Response(
                {"error": "No associated teacher profile found for this user.", "code": "TEACHER_PROFILE_MISSING"},
                status=status.HTTP_400_BAD_REQUEST
            )

        existing = ClassRoom.objects.filter(name__iexact=name, teacher=teacher_profile).first()
        if existing:
            return Response(ClassRoomSerializer(existing).data, status=status.HTTP_200_OK)

        classroom = ClassRoom.objects.create(
            name=name,
            teacher=teacher_profile
        )

        return Response(ClassRoomSerializer(classroom).data, status=status.HTTP_201_CREATED)


class TeacherClassRoomStudentsView(APIView):
    """
    GET /api/teacher/classrooms/{class_id}/students/
    Returns all students enrolled in this classroom.

    POST /api/teacher/classrooms/{class_id}/students/
    Enrolls a student into this classroom.
    Payload: {"student_id": "STU001"} or {"student_pk": 1}

    DELETE /api/teacher/classrooms/{class_id}/students/
    Unenrolls a student from this classroom.
    Payload: {"student_id": "STU001"} or {"student_pk": 1}
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, class_id):
        classroom = get_object_or_404(ClassRoom, id=class_id)
        if not is_authorized_teacher_or_staff(request.user, classroom):
            return Response({"error": "Not authorized to manage this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        students = classroom.get_enrolled_students(active_only=False).order_by("full_name")
        serializer = StudentProfileSerializer(students, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, class_id):
        classroom = get_object_or_404(ClassRoom, id=class_id)
        if not is_authorized_teacher_or_staff(request.user, classroom):
            return Response({"error": "Not authorized to manage this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        student_id = request.data.get("student_id")
        student_pk = request.data.get("student_pk")

        try:
            if student_id:
                student = Student.objects.get(student_id=student_id)
            elif student_pk:
                student = Student.objects.get(id=student_pk)
            else:
                return Response({"error": "Provide student_id or student_pk."}, status=status.HTTP_400_BAD_REQUEST)
        except Student.DoesNotExist:
            return Response({"error": f"Student '{student_id or student_pk}' not found."}, status=status.HTTP_404_NOT_FOUND)

        student.classrooms.add(classroom)
        if not student.class_room_id:
            student.class_room = classroom
            student.save(update_fields=["class_room"])

        return Response({
            "message": f"Student '{student.full_name}' successfully enrolled in {classroom.name}.",
            "student_id": student.student_id,
            "classroom_id": classroom.id,
            "enrolled_classes": [c.name for c in student.get_enrolled_classrooms()],
        }, status=status.HTTP_200_OK)

    def delete(self, request, class_id):
        classroom = get_object_or_404(ClassRoom, id=class_id)
        if not is_authorized_teacher_or_staff(request.user, classroom):
            return Response({"error": "Not authorized to manage this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        student_id = request.data.get("student_id")
        student_pk = request.data.get("student_pk")

        try:
            if student_id:
                student = Student.objects.get(student_id=student_id)
            elif student_pk:
                student = Student.objects.get(id=student_pk)
            else:
                return Response({"error": "Provide student_id or student_pk."}, status=status.HTTP_400_BAD_REQUEST)
        except Student.DoesNotExist:
            return Response({"error": f"Student '{student_id or student_pk}' not found."}, status=status.HTTP_404_NOT_FOUND)

        student.classrooms.remove(classroom)
        if student.class_room_id == classroom.id:
            other = student.classrooms.first()
            student.class_room = other
            student.save(update_fields=["class_room"])

        return Response({
            "message": f"Student '{student.full_name}' unenrolled from {classroom.name}.",
            "student_id": student.student_id,
            "classroom_id": classroom.id,
        }, status=status.HTTP_200_OK)


class SessionRotateQRView(APIView):
    """
    POST /api/teacher/sessions/{id}/qr/
    Generates or rotates a dynamic QR token for a session with an expiration timestamp.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to manage this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        if session.is_cancelled_or_inactive:
            return Response({"error": "Cannot rotate QR on a cancelled session.", "code": "SESSION_CANCELLED"}, status=status.HTTP_400_BAD_REQUEST)

        expiry_minutes = int(request.data.get("expiry_minutes", 10))
        now = timezone.now()
        session.qr_token = secrets.token_urlsafe(32)
        session.qr_token_expires_at = now + datetime.timedelta(minutes=expiry_minutes)
        update_fields = ["qr_token", "qr_token_expires_at"]
        if not session.started_at:
            session.started_at = now
            update_fields.append("started_at")
        session.save(update_fields=update_fields)

        return Response({
            "session_id": session.id,
            "qr_token": session.qr_token,
            "qr_token_expires_at": session.qr_token_expires_at,
            "expires_in_minutes": expiry_minutes,
            "started_at": session.started_at,
        })


class SessionDynamicQRView(APIView):
    """
    GET /api/teacher/sessions/{id}/qr/dynamic/
    Returns the currently active rotating QR token with remaining countdown seconds.
    Used by classroom projectors to refresh QR codes every 20 seconds.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to manage this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        if session.is_cancelled_or_inactive:
            return Response({"error": "This session has been cancelled.", "code": "SESSION_CANCELLED"}, status=status.HTTP_400_BAD_REQUEST)

        if session.ended_at:
            return Response({"error": "This session has already ended.", "code": "SESSION_ENDED"}, status=status.HTTP_400_BAD_REQUEST)

        if not session.started_at:
            session.started_at = timezone.now()
            session.save(update_fields=["started_at"])

        info = get_dynamic_qr_info(session.id)
        info["class_room"] = session.class_room.name
        info["date"] = str(session.date)
        info["start_time"] = str(session.start_time)
        return Response(info)


def session_live_qr(request, session_id):
    """
    GET /teacher/sessions/{id}/live-qr/
    Renders fullscreen teacher projector view with auto-rotating dynamic QR code.
    """
    from django.contrib.auth.decorators import login_required
    from django.shortcuts import render
    from attendance.models import AttendanceRecord

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    session = get_object_or_404(
        Session.objects.select_related("class_room", "class_room__teacher"),
        id=session_id
    )
    if not is_authorized_teacher_or_staff(request.user, session.class_room):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Not authorized to display QR for this session.")

    if not session.started_at:
        session.started_at = timezone.now()
        session.save(update_fields=["started_at"])

    enrolled_count = session.class_room.get_enrolled_students(active_only=True).count()
    present_count = AttendanceRecord.objects.filter(
        session=session, status__in=["present", "late"], is_deleted=False
    ).count()

    return render(request, "attendance/live_qr.html", {
        "session": session,
        "class_room": session.class_room,
        "teacher": session.class_room.teacher,
        "enrolled_count": enrolled_count,
        "present_count": present_count,
        "interval_seconds": getattr(settings, "DYNAMIC_QR_INTERVAL_SECONDS", 20),
    })


class SessionEndView(APIView):
    """
    POST /api/teacher/sessions/{id}/end/
    Marks a session as ended and triggers the asynchronous absence alert Celery task.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to end this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        if not session.ended_at:
            session.ended_at = timezone.now()
            session.save(update_fields=["ended_at"])

        # Trigger absence alert task (async with Celery if running, or background thread fallback)
        try:
            task_result = send_absence_alerts_for_session.delay(session.id)
            task_id = task_result.id
        except Exception as exc:
            logger.warning(f"Could not queue Celery task: {exc}. Dispatching in background thread.")
            import threading
            threading.Thread(
                target=send_absence_alerts_for_session,
                args=(session.id,),
                daemon=True,
                name=f"alert-end-session-{session.id}"
            ).start()
            task_id = "thread-dispatched"

        return Response({
            "message": "Session ended. Absence alerts have been triggered.",
            "session_id": session.id,
            "ended_at": session.ended_at,
            "task_id": task_id,
        })


class SessionReopenView(APIView):
    """
    POST /api/teacher/sessions/{id}/reopen/
    Reopens an accidentally ended session within a 30-minute grace window.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to manage this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        if not session.ended_at:
            return Response({
                "message": "Session is already active.",
                "session": SessionSerializer(session).data
            }, status=status.HTTP_200_OK)

        now = timezone.now()
        # Allow reopen within 30-minute grace window
        if now - session.ended_at > datetime.timedelta(minutes=30):
            return Response({
                "error": "Cannot reopen a session that ended more than 30 minutes ago.",
                "code": "REOPEN_WINDOW_EXPIRED"
            }, status=status.HTTP_400_BAD_REQUEST)

        session.ended_at = None
        session.qr_token_expires_at = now + datetime.timedelta(minutes=30)
        session.save(update_fields=["ended_at", "qr_token_expires_at"])

        return Response({
            "message": "Session reopened successfully. Attendance check-in is active again.",
            "session": SessionSerializer(session).data
        }, status=status.HTTP_200_OK)


class SessionCancelView(APIView):
    """
    POST /api/teacher/sessions/{id}/cancel/
    Cancels a scheduled or active session (e.g. holiday, teacher absence).
    Marks session as cancelled and prevents automatic absence alerts from triggering.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to cancel this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        now = timezone.now()
        session.is_cancelled = True
        session.qr_token = "CANCELLED"
        session.ended_at = now
        session.save(update_fields=["is_cancelled", "qr_token", "ended_at"])

        return Response({
            "message": "Session has been cancelled. No absence alerts will be dispatched.",
            "session_id": session.id,
            "status": "cancelled",
        }, status=status.HTTP_200_OK)


class AttendanceOverrideView(APIView):
    """
    PATCH /api/teacher/attendance/{id}/
    Manual override of an attendance record status or soft-delete. Tracks edited_by.
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, record_id):
        record = get_object_or_404(AttendanceRecord.objects.select_related("session__class_room"), id=record_id)
        if not is_authorized_teacher_or_staff(request.user, record.session.class_room):
            return Response({"error": "Not authorized to modify attendance for this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = AttendanceOverrideSerializer(record, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(edited_by=request.user)
        return Response(AttendanceRecordSerializer(record).data)


class TeacherSessionCreateView(APIView):
    """
    POST /api/teacher/sessions/
    Creates a new session for a teacher's classroom on demand with conflict detection.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = SessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = serializer.validated_data["class_room"]

        if not is_authorized_teacher_or_staff(request.user, classroom):
            return Response({"error": "Not authorized to create sessions for this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        target_date = serializer.validated_data.get("date", timezone.now().date())
        start_time = serializer.validated_data["start_time"]
        end_time = serializer.validated_data["end_time"]

        if start_time >= end_time:
            return Response({
                "error": "start_time must be strictly earlier than end_time.",
                "code": "INVALID_TIME_RANGE"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check for overlapping active sessions in this physical classroom
        overlapping_classroom = Session.objects.filter(
            class_room=classroom,
            date=target_date,
            start_time__lt=end_time,
            end_time__gt=start_time,
            is_cancelled=False,
        ).exclude(qr_token="CANCELLED").first()

        if overlapping_classroom:
            if overlapping_classroom.start_time == start_time and overlapping_classroom.end_time == end_time:
                # Idempotent return if client double-tapped create
                return Response(SessionSerializer(overlapping_classroom).data, status=status.HTTP_200_OK)
            return Response({
                "error": f"Classroom '{classroom.name}' already has a session scheduled from {overlapping_classroom.start_time.strftime('%H:%M')} to {overlapping_classroom.end_time.strftime('%H:%M')}.",
                "code": "CLASSROOM_OVERLAP_CONFLICT"
            }, status=status.HTTP_409_CONFLICT)

        # Check for teacher scheduling overlap in another classroom
        assigned_teacher = classroom.teacher or getattr(request.user, "teacher_profile", None)
        if assigned_teacher:
            overlapping_teacher = Session.objects.filter(
                Q(class_room__teacher=assigned_teacher) | Q(class_room__co_teachers=assigned_teacher),
                date=target_date,
                start_time__lt=end_time,
                end_time__gt=start_time,
                is_cancelled=False,
            ).exclude(qr_token="CANCELLED").exclude(class_room=classroom).first()

            if overlapping_teacher:
                return Response({
                    "error": f"Teacher is already scheduled to teach in '{overlapping_teacher.class_room.name}' from {overlapping_teacher.start_time.strftime('%H:%M')} to {overlapping_teacher.end_time.strftime('%H:%M')}.",
                    "code": "TEACHER_SCHEDULE_CONFLICT"
                }, status=status.HTTP_409_CONFLICT)

        session = serializer.save()
        return Response(SessionSerializer(session).data, status=status.HTTP_201_CREATED)


class TeacherSessionRosterView(APIView):
    """
    GET /api/teacher/sessions/{session_id}/roster/
    Returns all enrolled students in the session's classroom, showing their current attendance status.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room", "class_room__teacher"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to view roster for this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)


        classroom = session.class_room
        students = list(classroom.get_enrolled_students(active_only=True).order_by("full_name"))

        records = AttendanceRecord.objects.filter(session=session)
        record_map = {r.student_id: r for r in records}

        roster = []
        summary = {"present": 0, "late": 0, "absent": 0, "unmarked": 0, "total": len(students)}

        for st in students:
            rec = record_map.get(st.id)
            if rec and not rec.is_deleted:
                att_status = rec.status
                method = rec.method
                checked_in_at = rec.checked_in_at
                confidence_score = rec.confidence_score
                record_id = rec.id
                is_deleted = False
                summary[att_status] = summary.get(att_status, 0) + 1
            else:
                att_status = "unmarked"
                method = None
                checked_in_at = None
                confidence_score = None
                record_id = rec.id if rec else None
                is_deleted = rec.is_deleted if rec else False
                summary["unmarked"] += 1

            roster.append({
                "id": st.id,
                "student_id": st.student_id,
                "full_name": st.full_name,
                "is_active": st.is_active,
                "guardian_contact": st.guardian_contact,
                "guardian_email": st.effective_guardian_email or "",
                "guardian_phone": st.guardian_phone or "",
                "guardian_telegram_id": st.effective_guardian_telegram_id or "",
                "attendance_status": att_status,
                "method": method,
                "checked_in_at": checked_in_at,
                "confidence_score": confidence_score,
                "record_id": record_id,
                "is_deleted": is_deleted,
            })

        serializer = SessionRosterItemSerializer(roster, many=True)
        return Response({
            "session_id": session.id,
            "class_room": classroom.name,
            "date": session.date,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "is_ended": session.ended_at is not None,
            "summary": summary,
            "roster": serializer.data,
        })


class TeacherSessionLiveFeedView(APIView):
    """
    GET /api/teacher/sessions/{session_id}/live-feed/
    Optional Query: ?since=2026-09-23T08:00:00Z
    Provides a real-time attendance polling feed for teachers during active class sessions.
    Returns session attendance counters and newly recorded check-ins (e.g. from Kiosk or mobile).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id):
        session = get_object_or_404(
            Session.objects.select_related("class_room", "class_room__teacher"),
            id=session_id
        )
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response(
                {"error": "Not authorized to view live attendance for this session.", "code": "UNAUTHORIZED"},
                status=status.HTTP_403_FORBIDDEN
            )

        total_enrolled = session.class_room.get_enrolled_students(active_only=True).count()
        records_qs = AttendanceRecord.objects.filter(
            session=session, is_deleted=False
        ).select_related("student")

        since_str = request.query_params.get("since")
        if since_str:
            try:
                from datetime import datetime
                clean_str = since_str.replace("Z", "+00:00")
                since_dt = datetime.fromisoformat(clean_str)
                records_qs = records_qs.filter(checked_in_at__gt=since_dt)
            except Exception:
                pass

        counts = AttendanceRecord.objects.filter(session=session, is_deleted=False).aggregate(
            present_count=Count("id", filter=Q(status="present")),
            late_count=Count("id", filter=Q(status="late")),
            absent_count=Count("id", filter=Q(status="absent")),
        )
        present_count = counts["present_count"] or 0
        late_count = counts["late_count"] or 0
        absent_count = counts["absent_count"] or 0
        unmarked_count = max(0, total_enrolled - (present_count + late_count + absent_count))

        recent_records = []
        for r in records_qs.order_by("-checked_in_at")[:50]:
            recent_records.append({
                "record_id": r.id,
                "student_id": r.student.student_id,
                "student_name": r.student.full_name,
                "status": r.status,
                "method": r.method,
                "confidence_score": round(r.confidence_score, 4) if r.confidence_score is not None else None,
                "checked_in_at": r.checked_in_at.isoformat(),
            })

        return Response({
            "session_id": session.id,
            "class_room": session.class_room.name,
            "date": str(session.date),
            "is_ended": session.ended_at is not None,
            "total_enrolled": total_enrolled,
            "checked_in_count": present_count + late_count,
            "present_count": present_count,
            "late_count": late_count,
            "absent_count": absent_count,
            "unmarked_count": unmarked_count,
            "server_time": timezone.now().isoformat(),
            "recent_checkins": recent_records,
        })


class TeacherSessionBulkAttendanceView(APIView):
    """
    POST /api/teacher/sessions/{session_id}/attendance/bulk/
    Applies bulk manual attendance status updates for multiple students in a session.
    Payload:
    {
      "records": [
        {"student_id": "STU001", "status": "present"},
        {"student_id": "STU002", "status": "absent"}
      ]
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session.objects.select_related("class_room", "class_room__teacher"), id=session_id)
        if not is_authorized_teacher_or_staff(request.user, session.class_room):
            return Response({"error": "Not authorized to modify attendance for this session.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = BulkAttendanceOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        items = serializer.validated_data["records"]
        updated_count = 0
        errors = []

        with transaction.atomic():
            for item in items:
                student_id = item.get("student_id")
                student_pk = item.get("student_pk")
                new_status = item["status"]

                try:
                    if student_id:
                        student = Student.objects.filter(
                            Q(classrooms=session.class_room) | Q(class_room=session.class_room),
                            student_id=student_id
                        ).distinct().first()
                    elif student_pk:
                        student = Student.objects.filter(
                            Q(classrooms=session.class_room) | Q(class_room=session.class_room),
                            id=student_pk
                        ).distinct().first()
                    else:
                        errors.append("Provide either student_id or student_pk.")
                        continue

                    if not student:
                        errors.append(f"Student {student_id or student_pk} not found in this classroom.")
                        continue
                except Exception as exc:
                    errors.append(f"Error fetching student {student_id or student_pk}: {exc}")
                    continue

                AttendanceRecord.objects.update_or_create(
                    student=student,
                    session=session,
                    defaults={
                        "status": new_status,
                        "method": "manual",
                        "is_deleted": False,
                        "edited_by": request.user,
                    }
                )
                updated_count += 1

        return Response({
            "message": f"Successfully updated {updated_count} attendance records.",
            "updated_count": updated_count,
            "errors": errors,
        }, status=status.HTTP_200_OK)


# ----------------------------------------------------------------------
# Face Recognition: Enrollment & Check-in (Phase 4)
# ----------------------------------------------------------------------

class FaceEnrollView(APIView):
    """
    POST /api/face/enroll/
    Accepts 1 to 5 face photos at various angles, generates embeddings, and saves to StudentFace.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # Identify target student with role & tampering guard
        target_student = None
        if hasattr(request.user, "student_profile"):
            target_student = request.user.student_profile
            # Anti-tampering guard: students cannot silently re-enroll / substitute photos
            if target_student.face_embeddings.count() > 0:
                return Response({
                    "error": "Face biometric is already enrolled. For photo updates, please visit the administration desk.",
                    "code": "ALREADY_ENROLLED"
                }, status=status.HTTP_403_FORBIDDEN)
        elif "student_id" in request.data:
            if not request.user.is_staff and not hasattr(request.user, "teacher_profile"):
                return Response({
                    "error": "Only authorized staff or teachers can enroll biometric data for other students.",
                    "code": "UNAUTHORIZED"
                }, status=status.HTTP_403_FORBIDDEN)
            target_student = get_object_or_404(Student, student_id=request.data["student_id"])
        else:
            return Response({"error": "Specify student_id for enrollment."}, status=status.HTTP_400_BAD_REQUEST)

        images = request.FILES.getlist("images")
        if not images:
            single = request.FILES.get("image")
            if single:
                images = [single]

        if not images:
            return Response({"error": "Provide at least one face photo (field: 'image' or 'images')."}, status=status.HTTP_400_BAD_REQUEST)

        from recognition.services import FaceRecognitionUnavailable, embedding_from_upload

        enrolled_count = 0
        errors = []

        for idx, img in enumerate(images):
            try:
                vector, model_name = embedding_from_upload(img)
                StudentFace.objects.create(student=target_student, embedding=vector)
                enrolled_count += 1
            except FaceRecognitionUnavailable as exc:
                errors.append(f"Image {idx + 1}: {str(exc)}")
            except Exception as exc:
                errors.append(f"Image {idx + 1}: Unexpected error: {str(exc)}")

        if enrolled_count > 0:
            target_student.consent_given_at = timezone.now()
            target_student.save(update_fields=["consent_given_at", "updated_at"])
            try:
                from students.models import FaceEmbedding
                first_sf = target_student.face_embeddings.first()
                if first_sf:
                    FaceEmbedding.objects.update_or_create(
                        student=target_student,
                        defaults={"vector": first_sf.embedding, "model_name": "approved-onnx-model"}
                    )
            except Exception:
                pass

        return Response({
            "message": f"Successfully enrolled {enrolled_count} face template(s).",
            "enrolled_count": enrolled_count,
            "total_templates": target_student.face_embeddings.count(),
            "errors": errors if errors else None,
        }, status=status.HTTP_201_CREATED if enrolled_count > 0 else status.HTTP_400_BAD_REQUEST)


class FaceCheckInView(APIView):
    """
    POST /api/attendance/checkin/face/
    Body (multipart/form-data):
      image: <captured frame>
      session_id: <int, optional but recommended>

    Form-data: { "image": <file>, "session_id": <optional_int> }
    Performs anti-spoofing verification (3D depth, FFT moiré, texture) and matches face embedding.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "attendance_checkin"

    def post(self, request):
        if not is_client_ip_allowed(request):
            return Response(
                {"error": "Attendance check-in is restricted to the authorized campus network."},
                status=status.HTTP_403_FORBIDDEN
            )

        image = request.FILES.get("image")
        if not image:
            return Response({"error": "No camera image provided."}, status=status.HTTP_400_BAD_REQUEST)

        session_id = request.data.get("session_id")
        session = None
        if session_id:
            session = get_object_or_404(Session, id=session_id)

        from recognition.services import FaceRecognitionUnavailable, process_kiosk_frame

        try:
            target_vector, _, liveness = process_kiosk_frame(image)
        except FaceRecognitionUnavailable as exc:
            err_str = str(exc)
            return Response({
                "error": err_str,
                "is_spoof": "Spoof" in err_str or "Planar" in err_str or "moiré" in err_str
            }, status=status.HTTP_400_BAD_REQUEST)

        # Vectorized batch cosine similarity search (SIMD accelerated)
        target = np.asarray(target_vector, dtype=np.float32)
        target_norm = float(np.linalg.norm(target))

        candidates_qs = StudentFace.objects.select_related("student").filter(
            student__is_active=True,
            student__consent_given_at__isnull=False
        )
        if session:
            candidates_qs = candidates_qs.filter(
                Q(student__classrooms=session.class_room) | Q(student__class_room=session.class_room)
            ).distinct()

        best_student = None
        best_score = -1.0

        embeddings_list = []
        students_list = []
        for candidate in candidates_qs:
            try:
                emb = np.asarray(candidate.embedding, dtype=np.float32)
                if emb.shape == target.shape:
                    embeddings_list.append(emb)
                    students_list.append(candidate.student)
            except Exception:
                continue

        if embeddings_list and target_norm > 1e-10:
            matrix = np.array(embeddings_list, dtype=np.float32)
            matrix_norms = np.linalg.norm(matrix, axis=1)
            denoms = target_norm * matrix_norms
            denoms[denoms == 0] = 1e-10
            similarities = np.dot(matrix, target) / denoms
            best_idx = int(np.argmax(similarities))
            best_score = float(similarities[best_idx])
            best_student = students_list[best_idx]

        threshold = getattr(settings, "MATCH_THRESHOLD", 0.60)

        if not best_student or best_score < threshold:
            return Response({
                "matched": False,
                "score": round(best_score, 4),
                "threshold": threshold,
                "liveness": liveness,
                "message": "Face not recognized. Please use QR check-in or request teacher assistance.",
            }, status=status.HTTP_200_OK)

        # Kiosk debounce: if the same student was processed in the last 5 seconds, return cached response
        debounce_key = f"kiosk_debounce:{session.id if session else 'none'}:{best_student.id}"
        cached_response = cache.get(debounce_key)
        if cached_response:
            return Response(cached_response, status=status.HTTP_200_OK)

        # Verify authenticated student matches recognized face (if mobile app check-in)
        if hasattr(request.user, "student_profile"):
            if best_student.id != request.user.student_profile.id:
                return Response({
                    "matched": False,
                    "student_id": best_student.student_id,
                    "student_name": best_student.full_name,
                    "error": f"Face matches student '{best_student.full_name}', but you are logged in as '{request.user.student_profile.full_name}'.",
                    "code": "FACE_IDENTITY_MISMATCH",
                }, status=status.HTTP_403_FORBIDDEN)

        # If session is active, record attendance
        attendance_info = None
        if session:
            now = timezone.now()
            if session.ended_at:
                return Response({
                    "matched": True,
                    "student_id": best_student.student_id,
                    "student_name": best_student.full_name,
                    "error": "This session has already ended.",
                    "code": "SESSION_ENDED"
                }, status=status.HTTP_400_BAD_REQUEST)

            session_start_datetime = timezone.make_aware(
                datetime.datetime.combine(session.date, session.start_time),
                timezone.get_current_timezone()
            )
            max_duration_hours = getattr(settings, "SESSION_MAX_DURATION_HOURS", 4)
            session_cutoff = session_start_datetime + datetime.timedelta(hours=max_duration_hours)
            if session.date < now.date() or now > session_cutoff:
                if not session.ended_at:
                    session.ended_at = now
                    session.save(update_fields=["ended_at"])
                return Response({
                    "matched": True,
                    "student_id": best_student.student_id,
                    "student_name": best_student.full_name,
                    "error": "This session has expired. Check-in is no longer allowed.",
                    "code": "SESSION_EXPIRED"
                }, status=status.HTTP_400_BAD_REQUEST)

            # Guard against Bilocation / Impossible Travel
            fifteen_mins_ago = now - datetime.timedelta(minutes=15)
            conflicting_checkin = AttendanceRecord.objects.filter(
                student=best_student,
                status__in=["present", "late"],
                checked_in_at__gte=fifteen_mins_ago,
                is_deleted=False
            ).exclude(session=session).select_related("session__class_room").first()

            if conflicting_checkin:
                return Response({
                    "matched": True,
                    "student_id": best_student.student_id,
                    "student_name": best_student.full_name,
                    "error": f"Impossible check-in: Already checked in to {conflicting_checkin.session.class_room.name} less than 15 minutes ago.",
                    "code": "IMPOSSIBLE_TRAVEL"
                }, status=status.HTTP_409_CONFLICT)

            # Guard against Device Hopping (Buddy Punching on a single phone)
            device_id = request.data.get("device_id")
            if device_id:
                device_cache_key = f"session_device:{session.id}:{device_id}"
                bound_student_id = cache.get(device_cache_key)
                if bound_student_id and bound_student_id != best_student.id:
                    return Response({
                        "matched": True,
                        "student_id": best_student.student_id,
                        "student_name": best_student.full_name,
                        "error": "This physical device has already been used to check in another student for this session.",
                        "code": "DEVICE_REUSE_BLOCKED"
                    }, status=status.HTTP_409_CONFLICT)
                cache.set(device_cache_key, best_student.id, timeout=43200)

            # Calculate late vs present based on session actual started_at (or scheduled start_time) + threshold
            late_threshold_mins = getattr(settings, "LATE_THRESHOLD_MINUTES", 15)
            effective_start = session.started_at or session_start_datetime
            late_cutoff = effective_start + datetime.timedelta(minutes=late_threshold_mins)
            record_status = "late" if now > late_cutoff else "present"

            try:
                with transaction.atomic():
                    record, created = AttendanceRecord.objects.get_or_create(
                        student=best_student,
                        session=session,
                        defaults={
                            "status": record_status,
                            "method": "face",
                            "checked_in_at": now,
                            "confidence_score": round(best_score, 4),
                            "is_deleted": False,
                        }
                    )
            except IntegrityError:
                record = AttendanceRecord.objects.get(student=best_student, session=session)
                created = False

            if not created:
                if record.is_deleted:
                    record.is_deleted = False
                    record.status = record_status
                    record.method = "face"
                    record.confidence_score = round(best_score, 4)
                    record.checked_in_at = now
                    record.save()
                elif record.edited_by is not None:
                    return Response({
                        "matched": True,
                        "student_id": best_student.student_id,
                        "student_name": best_student.full_name,
                        "error": f"Attendance was manually marked by {record.edited_by.username} as '{record.status}'. Face check-in cannot override teacher's record.",
                        "code": "TEACHER_LOCKED",
                        "status": record.status,
                        "attendance": AttendanceRecordSerializer(record).data,
                    }, status=status.HTTP_409_CONFLICT)
                elif record.status == "absent":
                    # Upgrade from automated absent mark to late/present upon face check-in
                    record.status = record_status
                    record.method = "face"
                    record.confidence_score = round(best_score, 4)
                    record.checked_in_at = now
                    record.save()

            attendance_info = AttendanceRecordSerializer(record).data

        response_data = {
            "matched": True,
            "student_id": best_student.student_id,
            "student_name": best_student.full_name,
            "confidence_score": round(best_score, 4),
            "liveness": liveness,
            "attendance": attendance_info,
        }
        cache.set(debounce_key, response_data, timeout=5)
        return Response(response_data, status=status.HTTP_200_OK)



# ----------------------------------------------------------------------
# Reports (Phase 5)
# ----------------------------------------------------------------------

class ClassReportView(APIView):
    """
    GET /api/reports/class/{id}/
    Provides aggregated attendance statistics for a specific classroom.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, class_id):
        classroom = get_object_or_404(ClassRoom, id=class_id)
        if not is_authorized_teacher_or_staff(request.user, classroom):
            return Response({"error": "Not authorized to view analytics for this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        total_students = classroom.get_enrolled_students(active_only=True).count()

        today = timezone.localdate()
        conducted_sessions_q = (
            (Q(date__lt=today) | Q(date=today, start_time__lte=timezone.localtime().time()))
            & Q(is_cancelled=False)
            & ~Q(qr_token="CANCELLED")
        )
        total_sessions = classroom.sessions.filter(conducted_sessions_q).count()

        records = AttendanceRecord.objects.filter(
            session__class_room=classroom,
            is_deleted=False,
            session__is_cancelled=False,
        ).exclude(session__qr_token="CANCELLED").filter(
            session__date__lte=today
        )
        counts = records.aggregate(
            total_records=Count("id"),
            present_count=Count("id", filter=Q(status="present")),
            late_count=Count("id", filter=Q(status="late")),
            absent_count=Count("id", filter=Q(status="absent")),
        )

        total = counts["total_records"] or 1
        present_pct = round((counts["present_count"] / total) * 100, 1) if counts["total_records"] else 0.0
        late_pct = round((counts["late_count"] / total) * 100, 1) if counts["total_records"] else 0.0
        absent_pct = round((counts["absent_count"] / total) * 100, 1) if counts["total_records"] else 0.0

        return Response({
            "class_id": classroom.id,
            "class_name": classroom.name,
            "teacher": classroom.teacher.name if classroom.teacher else None,
            "total_enrolled_students": total_students,
            "total_sessions": total_sessions,
            "total_attendance_records": counts["total_records"],
            "present_count": counts["present_count"],
            "present_percentage": present_pct,
            "late_count": counts["late_count"],
            "late_percentage": late_pct,
            "absent_count": counts["absent_count"],
            "absent_percentage": absent_pct,
        })


class ClassReportExportCSVView(APIView):
    """
    GET /api/reports/class/{class_id}/export-csv/
    Streams a CSV file containing all attendance logs for a classroom.
    Supports optional filters: ?date_from=YYYY-MM-DD, ?date_to=YYYY-MM-DD, ?session_id=<id>
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, class_id):
        import csv
        from django.http import HttpResponse

        classroom = get_object_or_404(ClassRoom, id=class_id)
        if not is_authorized_teacher_or_staff(request.user, classroom):
            return Response({"error": "Not authorized to export attendance logs for this classroom.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        records = (
            AttendanceRecord.objects.select_related("student", "session")
            .filter(session__class_room=classroom, is_deleted=False, session__is_cancelled=False)
            .exclude(session__qr_token="CANCELLED")
            .order_by("-session__date", "-session__start_time", "student__student_id")
        )

        session_id = request.query_params.get("session_id")
        if session_id:
            records = records.filter(session_id=session_id)

        date_from = request.query_params.get("date_from")
        if date_from:
            records = records.filter(session__date__gte=date_from)

        date_to = request.query_params.get("date_to")
        if date_to:
            records = records.filter(session__date__lte=date_to)

        filename = f"attendance_report_{classroom.name.replace(' ', '_')}_{timezone.localtime().strftime('%Y%m%d')}.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            "Session Date",
            "Classroom",
            "Session Time",
            "Student ID",
            "Full Name",
            "Status",
            "Check-In Method",
            "Checked In At",
            "Confidence Score",
            "Edited By",
        ])

        for r in records:
            check_in_str = timezone.localtime(r.checked_in_at).strftime("%Y-%m-%d %H:%M:%S") if r.checked_in_at else "-"
            conf_str = f"{r.confidence_score:.4f}" if r.confidence_score is not None else "-"
            editor_str = r.edited_by.username if r.edited_by else "-"
            writer.writerow([
                str(r.session.date),
                classroom.name,
                f"{r.session.start_time} - {r.session.end_time}",
                r.student.student_id,
                r.student.full_name,
                r.get_status_display(),
                r.get_method_display(),
                check_in_str,
                conf_str,
                editor_str,
            ])

        return response


class StudentReportView(APIView):
    """
    GET /api/reports/student/{id}/
    Provides attendance summary and history for a specific student.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, student_id):
        student = get_object_or_404(Student, id=student_id)

        # Allow: Staff, Teacher of any of student's classrooms, or the student themselves
        is_self = hasattr(request.user, "student_profile") and request.user.student_profile.id == student.id
        teacher_profile = getattr(request.user, "teacher_profile", None)
        is_class_teacher = (
            teacher_profile is not None
            and student.get_enrolled_classrooms().filter(
                Q(teacher=teacher_profile) | Q(co_teachers=teacher_profile)
            ).exists()
        )
        if not (request.user.is_staff or is_self or is_class_teacher):
            return Response({"error": "Not authorized to view this student's report.", "code": "UNAUTHORIZED"}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.localdate()
        records = AttendanceRecord.objects.filter(
            student=student,
            is_deleted=False,
            session__is_cancelled=False,
        ).exclude(session__qr_token="CANCELLED").filter(
            session__date__lte=today
        )
        counts = records.aggregate(
            total_records=Count("id"),
            present_count=Count("id", filter=Q(status="present")),
            late_count=Count("id", filter=Q(status="late")),
            absent_count=Count("id", filter=Q(status="absent")),
        )

        total = counts["total_records"] or 1
        present_pct = round((counts["present_count"] / total) * 100, 1) if counts["total_records"] else 0.0

        enrolled_classes = student.get_enrolled_classrooms()
        primary_class = student.class_room or enrolled_classes.first()

        return Response({
            "student_id": student.student_id,
            "student_name": student.full_name,
            "class_room": primary_class.name if primary_class else None,
            "classrooms": [c.name for c in enrolled_classes],
            "total_recorded_sessions": counts["total_records"],
            "present_count": counts["present_count"],
            "late_count": counts["late_count"],
            "absent_count": counts["absent_count"],
            "attendance_rate": present_pct,
        })



# ----------------------------------------------------------------------
# API Root, Healthcheck & Interactive Docs
# ----------------------------------------------------------------------

class ApiRootView(APIView):
    """
    GET /api/
    Service discovery and endpoint index.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response({
            "name": "Smart Attendance System REST API",
            "version": "v1",
            "status": "operational",
            "documentation": "/api/docs/",
            "endpoints": {
                "auth": {
                    "login": "/api/auth/login/",
                    "logout": "/api/auth/logout/",
                    "change_password": "/api/auth/change-password/",
                },
                "students": {
                    "profile": "/api/students/me/",
                    "attendance_history": "/api/attendance/history/",
                    "qr_checkin": "/api/attendance/checkin/qr/",
                    "face_checkin": "/api/attendance/checkin/face/",
                    "face_enroll": "/api/face/enroll/",
                },
                "teachers": {
                    "today_classes": "/api/teacher/classes/today/",
                    "create_session": "/api/teacher/sessions/",
                    "session_roster": "/api/teacher/sessions/{id}/roster/",
                    "rotate_qr": "/api/teacher/sessions/{id}/qr/",
                    "dynamic_qr": "/api/teacher/sessions/{id}/qr/dynamic/",
                    "live_qr_screen": "/api/teacher/sessions/{id}/live-qr/",
                    "end_session": "/api/teacher/sessions/{id}/end/",
                    "override_attendance": "/api/teacher/attendance/{id}/",
                },
                "reports": {
                    "class_report": "/api/reports/class/{id}/",
                    "student_report": "/api/reports/student/{id}/",
                },
                "health": "/api/health/",
                "schema": "/api/schema/",
            }
        })


class HealthCheckView(APIView):
    """
    GET /api/health/
    System health status check for DB, Redis, Celery and Face Recognition.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from django.db import connection
        import redis

        # DB check
        db_ok = True
        db_err = None
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
        except Exception as exc:
            db_ok = False
            db_err = str(exc)

        # Redis check
        redis_ok = True
        redis_err = None
        try:
            r = redis.from_url(getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0"))
            r.ping()
        except Exception as exc:
            redis_ok = False
            redis_err = str(exc)

        # Face Recognition module check
        face_model_ok = True
        try:
            from recognition.services import get_face_app
            face_model_ok = True
        except Exception:
            face_model_ok = False

        overall_status = "healthy" if (db_ok and redis_ok) else "degraded"

        return Response({
            "status": overall_status,
            "timestamp": timezone.now().isoformat(),
            "services": {
                "database": {"status": "up" if db_ok else "down", "error": db_err},
                "redis": {"status": "up" if redis_ok else "down", "error": redis_err},
                "face_recognition": {"status": "ready" if face_model_ok else "warning"},
            }
        }, status=status.HTTP_200_OK if overall_status == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE)


def api_docs_view(request):
    """
    GET /api/docs/
    Renders interactive Swagger UI documentation console.
    """
    from django.shortcuts import render
    return render(request, "attendance/api_docs.html")


def api_schema_view(request):
    """
    GET /api/schema/
    Returns complete OpenAPI 3.0 specification in JSON format for Swagger UI.
    """
    from django.http import JsonResponse

    schema = {
        "openapi": "3.0.3",
        "info": {
            "title": "Smart Attendance System API",
            "version": "1.0.0",
            "description": "Comprehensive REST API documentation for Student & Teacher Attendance tracking, QR code check-in, Face Recognition biometric verification, Live Classroom Feeds, and Reports.",
        },
        "servers": [{"url": "/api", "description": "Current Server API Root"}],
        "tags": [
            {"name": "Auth", "description": "Authentication and user credentials"},
            {"name": "Students", "description": "Student profile, daily schedule, and alert history"},
            {"name": "Attendance", "description": "QR and Face biometric attendance check-in"},
            {"name": "Biometrics", "description": "Facial enrollment and biometric template registration"},
            {"name": "Teachers", "description": "Teacher sessions, live rosters, dynamic QR, and real-time feeds"},
            {"name": "Reports", "description": "Aggregated classroom analytics, CSV exports, and student metrics"},
            {"name": "System", "description": "Discovery index and service health check"},
        ],
        "components": {
            "securitySchemes": {
                "TokenAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "Authorization",
                    "description": "Enter token in format: Token <your_token>",
                }
            }
        },
        "paths": {
            "/": {
                "get": {
                    "tags": ["System"],
                    "summary": "API Discovery Root",
                    "description": "Returns a navigational index of all available API endpoints.",
                    "responses": {"200": {"description": "API navigation dictionary"}},
                }
            },
            "/health/": {
                "get": {
                    "tags": ["System"],
                    "summary": "System & Dependency Health Check",
                    "description": "Monitors connectivity to PostgreSQL database, Redis cache/broker, Celery worker pool, and InsightFace AI runtime.",
                    "responses": {"200": {"description": "Service health status dictionary"}},
                }
            },
            "/auth/login/": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Authenticate user & obtain API Token",
                    "description": "Validates username & password for students, teachers, or administrators. Returns unique DRF auth token and user profile role.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "username": {"type": "string", "example": "teacher_sokha"},
                                        "password": {"type": "string", "example": "TeacherPassword123!"},
                                    },
                                    "required": ["username", "password"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Auth token and user role profile"}},
                }
            },
            "/auth/logout/": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Revoke authentication token",
                    "description": "Deletes the caller's DRF token from the database, preventing discarded tokens from being replayed.",
                    "security": [{"TokenAuth": []}],
                    "responses": {"200": {"description": "Token revoked successfully"}},
                }
            },
            "/auth/change-password/": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Change account password",
                    "security": [{"TokenAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "old_password": {"type": "string"},
                                        "new_password": {"type": "string"},
                                        "confirm_password": {"type": "string"},
                                    },
                                    "required": ["old_password", "new_password", "confirm_password"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Password updated and new token issued"}},
                }
            },
            "/students/me/": {
                "get": {
                    "tags": ["Students"],
                    "summary": "Get authenticated student profile",
                    "description": "Returns profile details for the logged-in student including classroom, year, guardian contact, and face enrollment status.",
                    "security": [{"TokenAuth": []}],
                    "responses": {"200": {"description": "Student profile data"}},
                }
            },
            "/students/schedule/today/": {
                "get": {
                    "tags": ["Students"],
                    "summary": "Get today's class schedule for student",
                    "description": "Returns all scheduled sessions for the student's assigned classroom today, including check-in status and active QR indicator.",
                    "security": [{"TokenAuth": []}],
                    "responses": {"200": {"description": "List of scheduled sessions for today"}},
                }
            },
            "/alerts/mine/": {
                "get": {
                    "tags": ["Students"],
                    "summary": "Get student's absence alert history",
                    "description": "Returns notification logs sent to guardian/student for absences across past sessions.",
                    "security": [{"TokenAuth": []}],
                    "responses": {"200": {"description": "List of alert log entries"}},
                }
            },
            "/attendance/checkin/qr/": {
                "post": {
                    "tags": ["Attendance"],
                    "summary": "Student check-in via scanned QR token",
                    "description": "Validates dynamic rotating QR token or static token. Determines Present vs Late based on arrival time. Enforces campus IP subnet restrictions.",
                    "security": [{"TokenAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "qr_token": {"type": "string", "example": "dyn_1_abc12345"},
                                    },
                                    "required": ["qr_token"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Already checked in (idempotent)"},
                        "201": {"description": "Successfully checked in"},
                        "400": {"description": "Expired or invalid QR token"},
                        "403": {"description": "Campus IP restriction or unassigned student"},
                    },
                }
            },
            "/attendance/checkin/face/": {
                "post": {
                    "tags": ["Attendance"],
                    "summary": "Student check-in via live camera face frame",
                    "description": "Processes camera frame in RAM, runs 3D anti-spoofing detection, extracts 512D InsightFace embedding, and matches against classroom enrolled faces.",
                    "security": [{"TokenAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "image": {"type": "string", "format": "binary", "description": "Captured camera frame image (JPEG/PNG)"},
                                        "session_id": {"type": "integer", "description": "Optional active session ID to narrow match candidate space"},
                                    },
                                    "required": ["image"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Match result with student info and confidence score"},
                        "400": {"description": "Spoof detected or no face found"},
                        "429": {"description": "Rate limit exceeded (anti-hammering cooldown)"},
                    },
                }
            },
            "/attendance/history/": {
                "get": {
                    "tags": ["Attendance"],
                    "summary": "List student personal attendance history",
                    "description": "Returns paginated list of past attendance records for the logged-in student.",
                    "security": [{"TokenAuth": []}],
                    "parameters": [
                        {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["present", "late", "absent"]}},
                        {"name": "date_from", "in": "query", "schema": {"type": "string", "format": "date"}},
                        {"name": "date_to", "in": "query", "schema": {"type": "string", "format": "date"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}},
                    ],
                    "responses": {"200": {"description": "Paginated attendance history records"}},
                }
            },
            "/face/enroll/": {
                "post": {
                    "tags": ["Biometrics"],
                    "summary": "Enroll student biometric face photos (1–5 angles)",
                    "description": "Uploads 1 to 5 facial photos (front, left, right, up) to generate multi-angle 512D embeddings in StudentFace with privacy consent timestamp.",
                    "security": [{"TokenAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "images": {
                                            "type": "array",
                                            "items": {"type": "string", "format": "binary"},
                                            "description": "1 to 5 face image files at different angles",
                                        },
                                        "student_id": {"type": "string", "description": "Student ID (e.g. STU001) if enrolling on behalf of student"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Face templates extracted and enrolled successfully"}},
                }
            },
            "/teacher/classes/today/": {
                "get": {
                    "tags": ["Teachers"],
                    "summary": "List all sessions scheduled for today for teacher",
                    "security": [{"TokenAuth": []}],
                    "responses": {"200": {"description": "List of today's class sessions"}},
                }
            },
            "/teacher/sessions/": {
                "post": {
                    "tags": ["Teachers"],
                    "summary": "Create a new classroom session on-demand",
                    "security": [{"TokenAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "class_room": {"type": "integer", "example": 1},
                                        "date": {"type": "string", "format": "date", "example": "2026-09-23"},
                                        "start_time": {"type": "string", "example": "08:00:00"},
                                        "end_time": {"type": "string", "example": "10:00:00"},
                                        "auto_generate_qr": {"type": "boolean", "default": True},
                                        "qr_expiry_minutes": {"type": "integer", "default": 120},
                                    },
                                    "required": ["class_room", "date", "start_time", "end_time"],
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Session created successfully"}},
                }
            },
            "/teacher/sessions/{session_id}/roster/": {
                "get": {
                    "tags": ["Teachers"],
                    "summary": "Get full classroom student roster and check-in statuses",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Session roster breakdown and summary counts"}},
                }
            },
            "/teacher/sessions/{session_id}/live-feed/": {
                "get": {
                    "tags": ["Teachers"],
                    "summary": "Real-time live attendance polling feed for active session",
                    "description": "Returns live attendance counters (present, late, absent, unmarked) and incremental check-in events (e.g. from Kiosk or mobile). Supports `?since=<ISO_TIMESTAMP>`.",
                    "security": [{"TokenAuth": []}],
                    "parameters": [
                        {"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "since", "in": "query", "schema": {"type": "string", "format": "date-time"}, "description": "Optional ISO timestamp to fetch only check-ins after this time"},
                    ],
                    "responses": {"200": {"description": "Live attendance counters and recent check-ins"}},
                }
            },
            "/teacher/sessions/{session_id}/attendance/bulk/": {
                "post": {
                    "tags": ["Teachers"],
                    "summary": "Bulk override attendance statuses for multiple students",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "records": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "student_id": {"type": "string", "example": "STU001"},
                                                    "status": {"type": "string", "enum": ["present", "late", "absent"]},
                                                },
                                                "required": ["student_id", "status"],
                                            },
                                        }
                                    },
                                    "required": ["records"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Bulk attendance updated successfully"}},
                }
            },
            "/teacher/sessions/{session_id}/qr/": {
                "post": {
                    "tags": ["Teachers"],
                    "summary": "Rotate / refresh static session QR code",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"expiry_minutes": {"type": "integer", "default": 10}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "New QR token generated"}},
                }
            },
            "/teacher/sessions/{session_id}/qr/dynamic/": {
                "get": {
                    "tags": ["Teachers"],
                    "summary": "Get current dynamic rotating QR token with countdown",
                    "description": "Used by classroom projector screens to fetch the current rotating dynamic QR code token (rotates every 20 seconds).",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Active dynamic token and countdown remaining seconds"}},
                }
            },
            "/teacher/sessions/{session_id}/live-qr/": {
                "get": {
                    "tags": ["Teachers"],
                    "summary": "Fullscreen dynamic QR projector view (HTML)",
                    "description": "Renders an auto-refreshing fullscreen QR display designed for classroom projectors. Requires staff/teacher session login.",
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Projector HTML page with animated rotating QR code"}},
                }
            },
            "/teacher/sessions/{session_id}/end/": {
                "post": {
                    "tags": ["Teachers"],
                    "summary": "Finalize session & trigger automatic absence notifications",
                    "description": "Closes the class session, marks any unmarked students absent, and dispatches background Celery alerts via Telegram or Email.",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Session closed and absence alert Celery tasks dispatched"}},
                }
            },
            "/teacher/attendance/{record_id}/": {
                "patch": {
                    "tags": ["Teachers"],
                    "summary": "Manual single-record attendance override",
                    "description": "Allows teachers to update attendance status (present, late, absent) or soft-delete records with audit logging.",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "record_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string", "enum": ["present", "late", "absent"]},
                                        "is_deleted": {"type": "boolean"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Updated attendance record"}},
                }
            },
            "/reports/class/{class_id}/": {
                "get": {
                    "tags": ["Reports"],
                    "summary": "Aggregated attendance statistics for a classroom (JSON)",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "class_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Attendance percentage breakdown (present, late, absent)"}},
                }
            },
            "/reports/class/{class_id}/export-csv/": {
                "get": {
                    "tags": ["Reports"],
                    "summary": "Export classroom attendance report to CSV",
                    "description": "Downloads full attendance roster records as a CSV file with session date, student ID, status, check-in method, confidence score, and timestamp.",
                    "security": [{"TokenAuth": []}],
                    "parameters": [
                        {"name": "class_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "start_date", "in": "query", "schema": {"type": "string", "format": "date"}, "description": "Optional start date filter (YYYY-MM-DD)"},
                        {"name": "end_date", "in": "query", "schema": {"type": "string", "format": "date"}, "description": "Optional end date filter (YYYY-MM-DD)"},
                    ],
                    "responses": {
                        "200": {
                            "description": "CSV attachment file stream",
                            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
                        }
                    },
                }
            },
            "/reports/student/{student_id}/": {
                "get": {
                    "tags": ["Reports"],
                    "summary": "Aggregated attendance stats for a student",
                    "security": [{"TokenAuth": []}],
                    "parameters": [{"name": "student_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Individual student attendance rate and status metrics"}},
                }
            },
        },
    }
    return JsonResponse(schema)
