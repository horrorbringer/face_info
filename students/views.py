from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import EnrollmentForm, StudentImportForm, StudentSearchForm
from .models import FaceEmbedding, Student
from recognition.services import FaceRecognitionUnavailable, embedding_from_upload


@login_required
def manual_lookup(request):
    form = StudentSearchForm(request.GET or None)
    students = Student.objects.none()
    if form.is_valid():
        query = form.cleaned_data["query"]
        students = Student.objects.filter(Q(student_id__icontains=query) | Q(full_name__icontains=query), is_active=True)[:20]
    return render(request, "students/manual_lookup.html", {"form": form, "students": students})


@login_required
@permission_required("students.change_student", raise_exception=True)
def import_students(request):
    form = StudentImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            count = form.save()
        except Exception as exc:
            form.add_error("csv_file", str(exc))
        else:
            messages.success(request, f"Imported {count} student records.")
            return redirect("students:import")
    return render(request, "students/import.html", {"form": form})


@login_required
@permission_required("students.change_student", raise_exception=True)
def enroll(request, student_id):
    student = get_object_or_404(Student, student_id=student_id)
    form = EnrollmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            vector, model_name = embedding_from_upload(form.cleaned_data["image"])
        except FaceRecognitionUnavailable as exc:
            form.add_error("image", str(exc))
        else:
            student.consent_given_at = timezone.now()
            student.consent_reference = form.cleaned_data["consent_reference"]
            student.save(update_fields=["consent_given_at", "consent_reference", "updated_at"])
            FaceEmbedding.objects.update_or_create(student=student, defaults={"vector": vector, "model_name": model_name})
            messages.success(request, "Enrollment complete. The uploaded image was discarded.")
            return redirect("students:manual_lookup")
    return render(request, "students/enroll.html", {"form": form, "student": student})


@login_required
@permission_required("students.change_student", raise_exception=True)
def revoke(request, student_id):
    student = get_object_or_404(Student, student_id=student_id)
    if request.method == "POST":
        FaceEmbedding.objects.filter(student=student).delete()
        student.consent_given_at = None
        student.consent_reference = ""
        student.save(update_fields=["consent_given_at", "consent_reference", "updated_at"])
        messages.success(request, "Consent revoked and biometric template deleted.")
    return redirect("students:manual_lookup")

