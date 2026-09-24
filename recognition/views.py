from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from audits.models import LookupAuditLog
from .services import FaceRecognitionUnavailable, best_match, process_kiosk_frame


def check_kiosk_rate_limit(request, max_requests=45, window_secs=60):
    """
    Prevent camera frame hammering / CPU overload by rate-limiting frame submissions per IP.
    Returns (is_limited, client_ip).
    """
    ip = request.META.get("HTTP_X_FORWARDED_FOR")
    if ip:
        ip = ip.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "127.0.0.1")

    cache_key = f"kiosk_rate:{ip}"
    try:
        current = cache.get(cache_key, 0)
        if current >= max_requests:
            return True, ip
        cache.set(cache_key, current + 1, timeout=window_secs)
    except Exception:
        pass
    return False, ip


@login_required
def kiosk(request):
    from attendance.models import ClassRoom
    context = {"result": None}
    try:
        context["classrooms"] = ClassRoom.objects.all().order_by("name")
    except Exception:
        context["classrooms"] = []

    selected_classroom_id = request.POST.get("classroom_id") or request.GET.get("classroom_id")
    target_classroom = None
    if selected_classroom_id:
        try:
            target_classroom = ClassRoom.objects.filter(id=selected_classroom_id).first()
            context["selected_classroom_id"] = target_classroom.id if target_classroom else None
        except Exception:
            pass

    if request.method == "POST":
        image = request.FILES.get("image")
        prev_image = request.FILES.get("prev_image")
        is_ajax = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
            or request.POST.get("format") == "json"
        )

        limited, _ = check_kiosk_rate_limit(request)
        if limited:
            err = "Scanning too quickly. Please pause for a moment."
            if is_ajax:
                return JsonResponse({"success": False, "status": "rate_limited", "error": err}, status=429)
            context["error"] = err
            return render(request, "recognition/kiosk.html", context, status=429)

        if not image:
            err = "Capture a camera frame first."
            if is_ajax:
                return JsonResponse({"success": False, "status": "error", "error": err})
            context["error"] = err
            return render(request, "recognition/kiosk.html", context)

        try:
            vector, model_name, liveness = process_kiosk_frame(image, prev_upload=prev_image)
            student, score = best_match(vector, class_room=target_classroom)
            if not student and target_classroom:
                # Fallback to global match if student is enrolled in a different classroom
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

        # Optional: if an active class session exists for selected room or student's enrolled classrooms, record attendance
        session_recorded = False
        try:
            from attendance.models import AttendanceRecord, Session
            import datetime
            now = timezone.localtime()
            enrolled_rooms = [target_classroom] if target_classroom else list(student.get_enrolled_classrooms())
            if enrolled_rooms:
                session = Session.objects.filter(
                    class_room__in=enrolled_rooms,
                    date=now.date(),
                    ended_at__isnull=True,
                    is_cancelled=False,
                ).order_by("start_time").first()
                if session:
                    late_threshold_mins = getattr(settings, "LATE_THRESHOLD_MINUTES", 15)
                    session_start_datetime = timezone.make_aware(
                        datetime.datetime.combine(session.date, session.start_time),
                        timezone.get_current_timezone()
                    )
                    effective_start = session.started_at or session_start_datetime
                    late_cutoff = effective_start + datetime.timedelta(minutes=late_threshold_mins)
                    att_status = "late" if now > late_cutoff else "present"

                    AttendanceRecord.objects.get_or_create(
                        student=student,
                        session=session,
                        defaults={
                            "status": att_status,
                            "method": "face",
                            "checked_in_at": now,
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

