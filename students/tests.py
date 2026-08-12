from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import FaceEmbedding, Student


class StudentPrivacyTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="password")
        self.admin = User.objects.create_user("admin", password="password")
        self.admin.user_permissions.add(Permission.objects.get(codename="change_student"))
        self.student = Student.objects.create(student_id="S-001", full_name="Sok Dara", class_year="Year 2")

    def test_student_requires_consent_for_matching(self):
        self.assertFalse(self.student.can_be_matched)
        self.student.consent_given_at = timezone.now()
        self.assertTrue(self.student.can_be_matched)

    def test_manual_lookup_requires_login(self):
        response = self.client.get(reverse("students:manual_lookup"))
        self.assertEqual(response.status_code, 302)

    def test_revoke_deletes_biometric_template(self):
        self.student.consent_given_at = timezone.now()
        self.student.save()
        FaceEmbedding.objects.create(student=self.student, vector=[0.1, 0.2], model_name="test")
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse("students:revoke", args=[self.student.student_id]))
        self.assertRedirects(response, reverse("students:manual_lookup"))
        self.assertFalse(FaceEmbedding.objects.filter(student=self.student).exists())
        self.student.refresh_from_db()
        self.assertIsNone(self.student.consent_given_at)

