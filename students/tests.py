from unittest.mock import patch
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from attendance.models import StudentFace
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

    def test_enroll_get_renders_page(self):
        self.client.login(username="admin", password="password")
        response = self.client.get(reverse("students:enroll", args=[self.student.student_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sok Dara")
        self.assertContains(response, "Not Enrolled")

    def test_enroll_creates_both_biometric_models(self):
        self.client.login(username="admin", password="password")
        dummy_vector = [0.1] * 512

        with patch("students.views.embedding_from_upload") as mock_embed:
            mock_embed.return_value = (dummy_vector, "approved-onnx-model")
            fake_img = SimpleUploadedFile("face.jpg", b"dummy image content", content_type="image/jpeg")

            response = self.client.post(
                reverse("students:enroll", args=[self.student.student_id]),
                {
                    "consent_reference": "CONSENT-TEST-001",
                    "consent_confirmed": True,
                    "image": fake_img,
                }
            )
            self.assertRedirects(response, reverse("students:manual_lookup"))

        # Verify StudentFace was created (for attendance check-in)
        self.assertEqual(StudentFace.objects.filter(student=self.student).count(), 1)
        # Verify FaceEmbedding was created (for kiosk cosine search)
        self.assertTrue(FaceEmbedding.objects.filter(student=self.student).exists())

        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.consent_given_at)
        self.assertEqual(self.student.consent_reference, "CONSENT-TEST-001")

    def test_enroll_multiple_angles(self):
        self.client.login(username="admin", password="password")
        dummy_vector = [0.1] * 512

        with patch("students.views.embedding_from_upload") as mock_embed:
            mock_embed.return_value = (dummy_vector, "approved-onnx-model")
            fake_img1 = SimpleUploadedFile("face1.jpg", b"dummy 1", content_type="image/jpeg")
            fake_img2 = SimpleUploadedFile("face2.jpg", b"dummy 2", content_type="image/jpeg")
            fake_img3 = SimpleUploadedFile("face3.jpg", b"dummy 3", content_type="image/jpeg")

            response = self.client.post(
                reverse("students:enroll", args=[self.student.student_id]),
                {
                    "consent_reference": "CONSENT-MULTI-001",
                    "consent_confirmed": True,
                    "images": [fake_img1, fake_img2, fake_img3],
                }
            )
            self.assertRedirects(response, reverse("students:manual_lookup"))

        self.assertEqual(StudentFace.objects.filter(student=self.student).count(), 3)
        self.assertTrue(FaceEmbedding.objects.filter(student=self.student).exists())

    def test_revoke_get_renders_confirmation_page(self):
        self.client.login(username="admin", password="password")
        response = self.client.get(reverse("students:revoke", args=[self.student.student_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "students/revoke.html")
        self.assertContains(response, "Revoke Biometric Consent")

    def test_revoke_deletes_both_biometric_models(self):
        self.student.consent_given_at = timezone.now()
        self.student.save()
        FaceEmbedding.objects.create(student=self.student, vector=[0.1, 0.2], model_name="test")
        StudentFace.objects.create(student=self.student, embedding=[0.1, 0.2])

        self.client.login(username="admin", password="password")
        response = self.client.post(reverse("students:revoke", args=[self.student.student_id]))
        self.assertRedirects(response, reverse("students:manual_lookup"))

        self.assertFalse(FaceEmbedding.objects.filter(student=self.student).exists())
        self.assertFalse(StudentFace.objects.filter(student=self.student).exists())
        self.student.refresh_from_db()
        self.assertIsNone(self.student.consent_given_at)

