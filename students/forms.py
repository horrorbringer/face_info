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
    image = forms.ImageField(help_text="A temporary frame; it is processed and discarded.")
    consent_reference = forms.CharField(max_length=255, help_text="Consent form/reference number")

