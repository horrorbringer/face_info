from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from audits.models import LookupAuditLog
from .services import FaceRecognitionUnavailable, best_match, embedding_from_upload
from django.conf import settings


@login_required
def kiosk(request):
    context = {"result": None}
    if request.method == "POST":
        image = request.FILES.get("image")
        if not image:
            context["error"] = "Capture a camera frame first."
        else:
            try:
                vector, _ = embedding_from_upload(image)
                student, score = best_match(vector)
            except FaceRecognitionUnavailable as exc:
                context["error"] = str(exc)
            else:
                if student is None or score < settings.MATCH_THRESHOLD:
                    LookupAuditLog.objects.create(staff_user=request.user, outcome="no_match")
                    context["error"] = "No confident match. Please use manual lookup."
                else:
                    context["result"] = {"student": student, "score": score}
                    LookupAuditLog.objects.create(staff_user=request.user, student=student, outcome="review")
    return render(request, "recognition/kiosk.html", context)


@login_required
def confirm(request, student_id):
    from students.models import Student
    student = Student.objects.filter(student_id=student_id, is_active=True).first()
    if request.method == "POST" and student:
        LookupAuditLog.objects.create(staff_user=request.user, student=student, outcome="matched")
        return render(request, "recognition/confirmed.html", {"student": student})
    return render(request, "recognition/kiosk.html", {"error": "Unable to confirm this match."})

