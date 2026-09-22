from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from audits.models import LookupAuditLog
from .services import FaceRecognitionUnavailable, best_match, process_kiosk_frame


@login_required
def kiosk(request):
    context = {"result": None}
    if request.method == "POST":
        image = request.FILES.get("image")
        prev_image = request.FILES.get("prev_image")
        is_ajax = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
            or request.POST.get("format") == "json"
        )

        if not image:
            err = "Capture a camera frame first."
            if is_ajax:
                return JsonResponse({"success": False, "status": "error", "error": err})
            context["error"] = err
            return render(request, "recognition/kiosk.html", context)

        try:
            vector, model_name, liveness = process_kiosk_frame(image, prev_upload=prev_image)
            student, score = best_match(vector)
        except FaceRecognitionUnavailable as exc:
            err_msg = str(exc)
            status_code = "spoof" if "Spoof" in err_msg else "error"
            if status_code == "spoof":
                LookupAuditLog.objects.create(staff_user=request.user, outcome="no_match")
            if is_ajax:
                return JsonResponse({"success": False, "status": status_code, "error": err_msg})
            context["error"] = err_msg
            return render(request, "recognition/kiosk.html", context)

        # Confidence match threshold validation
        if student is None or score < settings.MATCH_THRESHOLD:
            LookupAuditLog.objects.create(staff_user=request.user, outcome="no_match")
            err_msg = "No confident match. Please use manual lookup or try again."
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "status": "no_match",
                    "score": round(score, 4) if score is not None else 0.0,
                    "error": err_msg,
                })
            context["error"] = err_msg
            return render(request, "recognition/kiosk.html", context)

        # AUTO-CONFIRM: Verified real face & matched enrolled student
        LookupAuditLog.objects.create(staff_user=request.user, student=student, outcome="matched")

        # Optional: if an active class session exists for student's classroom, mark present
        session_recorded = False
        try:
            from attendance.models import AttendanceRecord, Session
            now = timezone.localtime()
            if student.class_room:
                session = Session.objects.filter(
                    class_room=student.class_room,
                    date=now.date(),
                    ended_at__isnull=True,
                ).first()
                if session:
                    AttendanceRecord.objects.get_or_create(
                        student=student,
                        session=session,
                        defaults={
                            "status": "present",
                            "method": "face",
                            "confidence_score": score,
                            "edited_by": request.user,
                        }
                    )
                    session_recorded = True
        except Exception:
            session_recorded = False

        if is_ajax:
            return JsonResponse({
                "success": True,
                "status": "matched",
                "student": {
                    "student_id": student.student_id,
                    "full_name": student.full_name,
                    "class_year": student.class_year or "Not recorded",
                    "class_room": student.class_room.name if student.class_room else "Unassigned",
                },
                "score": round(score, 4),
                "liveness": liveness,
                "session_recorded": session_recorded,
            })

        # Regular form submit renders confirmed view directly without manual confirmation step
        return render(request, "recognition/confirmed.html", {
            "student": student,
            "score": round(score, 4),
            "liveness": liveness,
            "session_recorded": session_recorded,
            "auto_confirmed": True,
        })

    return render(request, "recognition/kiosk.html", context)


@login_required
def confirm(request, student_id):
    """Legacy confirmation endpoint kept for backward compatibility."""
    from students.models import Student
    student = Student.objects.filter(student_id=student_id, is_active=True).first()
    if request.method == "POST" and student:
        LookupAuditLog.objects.create(staff_user=request.user, student=student, outcome="matched")
        return render(request, "recognition/confirmed.html", {"student": student})
    return render(request, "recognition/kiosk.html", {"error": "Unable to confirm this match."})

