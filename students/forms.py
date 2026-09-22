import csv
from io import TextIOWrapper
from django import forms
from django.utils import timezone
from .models import Student


class StudentSearchForm(forms.Form):
    query = forms.CharField(label="Student ID or name", max_length=255)


class StudentImportForm(forms.Form):
    csv_file = forms.FileField(help_text="Required columns: student_id, full_name, class_year")

    def clean_csv_file(self):
        file = self.cleaned_data["csv_file"]
        if not file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a CSV file.")
        return file

    def save(self):
        uploaded = self.cleaned_data["csv_file"]
        reader = csv.DictReader(TextIOWrapper(uploaded.file, encoding="utf-8-sig"))
        required = {"student_id", "full_name", "class_year"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise forms.ValidationError("CSV must contain: student_id, full_name, class_year.")
        count = 0
        for line, row in enumerate(reader, start=2):
            student_id = (row.get("student_id") or "").strip()
            full_name = (row.get("full_name") or "").strip()
            if not student_id or not full_name:
                raise forms.ValidationError(f"Line {line}: student_id and full_name are required.")
            Student.objects.update_or_create(student_id=student_id, defaults={
                "full_name": full_name, "class_year": (row.get("class_year") or "").strip(),
            })
            count += 1
        return count


class EnrollmentForm(forms.Form):
    consent_reference = forms.CharField(
        max_length=255,
        label="Consent Reference",
        help_text="Consent form number, signed document ID, or parental record reference.",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g., CONSENT-2026-001",
            "id": "id_consent_reference"
        }),
    )
    consent_confirmed = forms.BooleanField(
        required=True,
        label="I confirm that verified student / guardian biometric consent is on file.",
        widget=forms.CheckboxInput(attrs={
            "class": "form-check-input",
            "id": "id_consent_confirmed"
        }),
    )
    image = forms.FileField(
        required=False,
        label="Face Image",
        help_text="Upload 1–5 angle photos (JPG, PNG) or capture frames via the live camera.",
        widget=forms.FileInput(attrs={
            "class": "form-control",
            "accept": "image/*",
            "id": "id_image"
        }),
    )

    def clean(self):
        cleaned_data = super().clean()
        # Ensure at least one image file is provided either via 'image' or 'images' in request.FILES
        has_file = bool(cleaned_data.get("image"))
        if not has_file and self.files:
            has_file = bool(self.files.getlist("images") or self.files.getlist("image"))
        if not has_file:
            self.add_error("image", "Please provide at least one face photo via camera capture or file upload.")
        return cleaned_data

