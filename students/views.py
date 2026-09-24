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
        query = form.cleaned_data["query"].strip()
        if query:
            students = (
                Student.objects.filter(
                    Q(student_id__icontains=query) | Q(full_name__icontains=query),
                    is_active=True,
                )
                .select_related("class_room")[:20]
            )
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
    from attendance.models import StudentFace
    student = get_object_or_404(Student, student_id=student_id)
    form = EnrollmentForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        # Retrieve all uploaded images from either 'images' (multi-angle or webcam snapshots) or 'image'
        images = request.FILES.getlist("images")
        if not images:
            single = request.FILES.get("image") or form.cleaned_data.get("image")
            if single:
                images = [single]

        if not images:
            form.add_error("image", "Please provide at least one face photo via camera capture or file upload.")
        else:
            enrolled_count = 0
            errors = []
            primary_vector = None
            primary_model = None

            for idx, img in enumerate(images):
                try:
                    vector, model_name = embedding_from_upload(img)
                    StudentFace.objects.create(student=student, embedding=vector)
                    if primary_vector is None:
                        primary_vector = vector
                        primary_model = model_name
                    enrolled_count += 1
                except FaceRecognitionUnavailable as exc:
                    errors.append(f"Photo {idx + 1}: {str(exc)}")
                except Exception as exc:
                    errors.append(f"Photo {idx + 1}: {str(exc)}")

            if enrolled_count > 0:
                student.consent_given_at = timezone.now()
                student.consent_reference = form.cleaned_data["consent_reference"]
                student.save(update_fields=["consent_given_at", "consent_reference", "updated_at"])

                # Keep FaceEmbedding in sync for kiosk / pilot search
                FaceEmbedding.objects.update_or_create(
                    student=student,
                    defaults={"vector": primary_vector, "model_name": primary_model or "approved-onnx-model"},
                )

                if errors:
                    messages.warning(
                        request,
                        f"Enrolled {enrolled_count} face template(s) with warnings: {'; '.join(errors)}"
                    )
                else:
                    messages.success(
                        request,
                        f"Enrollment complete. Successfully saved {enrolled_count} biometric template(s)."
                    )
                return redirect("students:manual_lookup")
            else:
                for err in errors:
                    form.add_error(None, err)

    # Compute template stats for the student profile card
    templates_count = student.face_embeddings.count()
    has_legacy = hasattr(student, "face_embedding")
    total_templates = max(templates_count, 1 if has_legacy else 0)
    is_enrolled = total_templates > 0 and student.consent_given_at is not None

    context = {
        "form": form,
        "student": student,
        "is_enrolled": is_enrolled,
        "total_templates": total_templates,
    }
    return render(request, "students/enroll.html", context)


@login_required
@permission_required("students.change_student", raise_exception=True)
def revoke(request, student_id):
    from attendance.models import StudentFace
    student = get_object_or_404(Student, student_id=student_id)
    if request.method == "POST":
        FaceEmbedding.objects.filter(student=student).delete()
        StudentFace.objects.filter(student=student).delete()
        student.consent_given_at = None
        student.consent_reference = ""
        student.save(update_fields=["consent_given_at", "consent_reference", "updated_at"])
        messages.success(request, f"Consent revoked and all biometric templates deleted for {student.full_name}.")
        return redirect("students:manual_lookup")
    return render(request, "students/revoke.html", {"student": student})

