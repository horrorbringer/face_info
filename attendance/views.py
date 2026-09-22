import datetime
import logging
import secrets
import numpy as np
from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from students.models import Student
from .models import AttendanceRecord, ClassRoom, Session, StudentFace, Teacher
from .security import (
    get_dynamic_qr_info,
    is_client_ip_allowed,
    verify_dynamic_qr_token,
)
from .serializers import (
    AttendanceOverrideSerializer,
    AttendanceRecordSerializer,
    ClassRoomSerializer,
    LoginSerializer,
    QRCheckInSerializer,
    SessionSerializer,
    StudentProfileSerializer,
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
            return Response({"error": "Only registered students can check in via QR code."}, status=status.HTTP_403_FORBIDDEN)
        student = request.user.student_profile

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
                    {"error": "Dynamic QR code has expired or is invalid. Please scan the current code on screen."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            try:
                session = Session.objects.select_related("class_room").get(qr_token=qr_token)
            except Session.DoesNotExist:
                return Response({"error": "Invalid QR code."}, status=status.HTTP_400_BAD_REQUEST)

            now = timezone.now()
            if not session.qr_token_expires_at or now > session.qr_token_expires_at:
                return Response({"error": "QR code has expired. Ask your teacher for a refreshed code."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        if session.ended_at:
            return Response({"error": "This session has already ended."}, status=status.HTTP_400_BAD_REQUEST)

        # Enforce student enrollment in this session's class (if class_room set)
        if student.class_room and student.class_room_id != session.class_room_id:
            return Response({"error": f"You are not enrolled in {session.class_room.name}."}, status=status.HTTP_403_FORBIDDEN)

        # Calculate late vs present based on session start_time + threshold
        late_threshold_mins = getattr(settings, "LATE_THRESHOLD_MINUTES", 15)
        session_start_datetime = timezone.make_aware(
            datetime.datetime.combine(session.date, session.start_time),
            timezone.get_current_timezone()
        )
        late_cutoff = session_start_datetime + datetime.timedelta(minutes=late_threshold_mins)

        record_status = "late" if now > late_cutoff else "present"

        record, created = AttendanceRecord.objects.get_or_create(
            student=student,
            session=session,
            defaults={
                "status": record_status,
                "method": "qr",
                "is_deleted": False,
            }
        )

        if not created:
            if record.is_deleted:
                record.is_deleted = False
                record.status = record_status
                record.method = "qr"
                record.save()
            else:
                return Response({
                    "message": "Already checked in for this session.",
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
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not hasattr(request.user, "student_profile"):
            return Response({"error": "No student profile found."}, status=status.HTTP_404_NOT_FOUND)
        student = request.user.student_profile
        records = AttendanceRecord.objects.filter(student=student, is_deleted=False).select_related("session", "session__class_room").order_by("-session__date", "-session__start_time")
        serializer = AttendanceRecordSerializer(records, many=True)
        return Response(serializer.data)


# ----------------------------------------------------------------------
# Teacher Session Management & Overrides (Phases 2, 3, 6)
# ----------------------------------------------------------------------

class TeacherTodayClassesView(APIView):
    """
    GET /api/teacher/classes/today/
    Returns all sessions for today for the authenticated teacher.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        today = timezone.localdate()
        sessions = Session.objects.filter(date=today).select_related("class_room")
        if hasattr(request.user, "teacher_profile") and not request.user.is_staff:
            sessions = sessions.filter(class_room__teacher=request.user.teacher_profile)
        serializer = SessionSerializer(sessions, many=True)
        return Response(serializer.data)


class SessionRotateQRView(APIView):
    """
    POST /api/teacher/sessions/{id}/qr/
    Generates or rotates a dynamic QR token for a session with an expiration timestamp.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session, id=session_id)
        # Check permissions: teacher of class or staff
        if hasattr(request.user, "teacher_profile") and not request.user.is_staff:
            if session.class_room.teacher != request.user.teacher_profile:
                return Response({"error": "Not authorized to manage this session."}, status=status.HTTP_403_FORBIDDEN)

        expiry_minutes = int(request.data.get("expiry_minutes", 10))
        session.qr_token = secrets.token_urlsafe(32)
        session.qr_token_expires_at = timezone.now() + datetime.timedelta(minutes=expiry_minutes)
        session.save(update_fields=["qr_token", "qr_token_expires_at"])

        return Response({
            "session_id": session.id,
            "qr_token": session.qr_token,
            "qr_token_expires_at": session.qr_token_expires_at,
            "expires_in_minutes": expiry_minutes,
        })


class SessionDynamicQRView(APIView):
    """
    GET /api/teacher/sessions/{id}/qr/dynamic/
    Returns the currently active rotating QR token with remaining countdown seconds.
    Used by classroom projectors to refresh QR codes every 20 seconds.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id):
        session = get_object_or_404(Session, id=session_id)
        if hasattr(request.user, "teacher_profile") and not request.user.is_staff:
            if session.class_room.teacher != request.user.teacher_profile:
                return Response({"error": "Not authorized to manage this session."}, status=status.HTTP_403_FORBIDDEN)

        if session.ended_at:
            return Response({"error": "This session has already ended."}, status=status.HTTP_400_BAD_REQUEST)

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

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    session = get_object_or_404(Session, id=session_id)
    if hasattr(request.user, "teacher_profile") and not request.user.is_staff:
        if session.class_room.teacher != request.user.teacher_profile:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Not authorized to display QR for this session.")

    return render(request, "attendance/live_qr.html", {
        "session": session,
        "class_room": session.class_room,
        "interval_seconds": getattr(settings, "DYNAMIC_QR_INTERVAL_SECONDS", 20),
    })


class SessionEndView(APIView):
    """
    POST /api/teacher/sessions/{id}/end/
    Marks a session as ended and triggers the asynchronous absence alert Celery task.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(Session, id=session_id)
        if hasattr(request.user, "teacher_profile") and not request.user.is_staff:
            if session.class_room.teacher != request.user.teacher_profile:
                return Response({"error": "Not authorized to end this session."}, status=status.HTTP_403_FORBIDDEN)

        if not session.ended_at:
            session.ended_at = timezone.now()
            session.save(update_fields=["ended_at"])

        # Trigger absence alert task (async with Celery if running, or synchronous fallback)
        try:
            task_result = send_absence_alerts_for_session.delay(session.id)
            task_id = task_result.id
        except Exception as exc:
            logger.warning(f"Could not queue Celery task, running synchronously: {exc}")
            send_absence_alerts_for_session(session.id)
            task_id = "synced"

        return Response({
            "message": "Session ended. Absence alerts have been triggered.",
            "session_id": session.id,
            "ended_at": session.ended_at,
            "task_id": task_id,
        })


class AttendanceOverrideView(APIView):
    """
    PATCH /api/teacher/attendance/{id}/
    Manual override of an attendance record status or soft-delete. Tracks edited_by.
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, record_id):
        record = get_object_or_404(AttendanceRecord, id=record_id)
        serializer = AttendanceOverrideSerializer(record, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(edited_by=request.user)
        return Response(AttendanceRecordSerializer(record).data)


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
        # Identify target student
        target_student = None
        if hasattr(request.user, "student_profile"):
            target_student = request.user.student_profile
        elif "student_id" in request.data:
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

        # Narrow search space to students in the session's class (or all active students if no session)
        target = np.asarray(target_vector, dtype=np.float32)
        candidates_qs = StudentFace.objects.select_related("student").filter(student__is_active=True)
        if session:
            candidates_qs = candidates_qs.filter(student__class_room=session.class_room)

        best_student = None
        best_score = -1.0

        for candidate in candidates_qs:
            stored = np.asarray(candidate.embedding, dtype=np.float32)
            if stored.shape != target.shape:
                continue
            denom = float(np.linalg.norm(target) * np.linalg.norm(stored))
            if denom == 0:
                continue
            sim = float(np.dot(target, stored) / denom)
            if sim > best_score:
                best_score = sim
                best_student = candidate.student

        threshold = getattr(settings, "MATCH_THRESHOLD", 0.5)

        if not best_student or best_score < threshold:
            return Response({
                "matched": False,
                "score": round(best_score, 4),
                "threshold": threshold,
                "liveness": liveness,
                "message": "Face not recognized. Please use QR check-in or request teacher assistance.",
            }, status=status.HTTP_200_OK)

        # If session is active, record attendance
        attendance_info = None
        if session:
            now = timezone.now()
            late_threshold_mins = getattr(settings, "LATE_THRESHOLD_MINUTES", 15)
            session_start_datetime = timezone.make_aware(
                datetime.datetime.combine(session.date, session.start_time),
                timezone.get_current_timezone()
            )
            record_status = "late" if now > (session_start_datetime + datetime.timedelta(minutes=late_threshold_mins)) else "present"

            record, _ = AttendanceRecord.objects.get_or_create(
                student=best_student,
                session=session,
                defaults={
                    "status": record_status,
                    "method": "face",
                    "confidence_score": round(best_score, 4),
                    "is_deleted": False,
                }
            )
            attendance_info = AttendanceRecordSerializer(record).data

        return Response({
            "matched": True,
            "student_id": best_student.student_id,
            "student_name": best_student.full_name,
            "confidence_score": round(best_score, 4),
            "liveness": liveness,
            "attendance": attendance_info,
        }, status=status.HTTP_200_OK)


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
        total_students = classroom.students.filter(is_active=True).count()
        total_sessions = classroom.sessions.count()

        records = AttendanceRecord.objects.filter(session__class_room=classroom, is_deleted=False)
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


class StudentReportView(APIView):
    """
    GET /api/reports/student/{id}/
    Provides attendance summary and history for a specific student.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, student_id):
        student = get_object_or_404(Student, id=student_id)
        records = AttendanceRecord.objects.filter(student=student, is_deleted=False)
        counts = records.aggregate(
            total_records=Count("id"),
            present_count=Count("id", filter=Q(status="present")),
            late_count=Count("id", filter=Q(status="late")),
            absent_count=Count("id", filter=Q(status="absent")),
        )

        total = counts["total_records"] or 1
        present_pct = round((counts["present_count"] / total) * 100, 1) if counts["total_records"] else 0.0

        return Response({
            "student_id": student.student_id,
            "student_name": student.full_name,
            "class_room": student.class_room.name if student.class_room else None,
            "total_recorded_sessions": counts["total_records"],
            "present_count": counts["present_count"],
            "late_count": counts["late_count"],
            "absent_count": counts["absent_count"],
            "attendance_rate": present_pct,
        })
