from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from audits.models import LookupAuditLog
from students.models import FaceEmbedding, Student


class KioskTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="password")
        self.student = Student.objects.create(
            student_id="STU-001",
            full_name="Sok Dara",
            class_year="Year 2",
            is_active=True,
            consent_given_at=timezone.now(),
        )

    def test_kiosk_requires_staff_login(self):
        self.assertEqual(self.client.get(reverse("recognition:kiosk")).status_code, 302)

    def test_unconfigured_recognition_explains_manual_fallback(self):
        self.client.login(username="staff", password="password")
        response = self.client.post(reverse("recognition:kiosk"), {})
        self.assertContains(response, "Capture a camera frame first.")

    def test_kiosk_auto_confirms_on_match_json(self):
        dummy_vector = [0.1] * 512
        FaceEmbedding.objects.create(student=self.student, vector=dummy_vector, model_name="approved-onnx-model")
        self.client.login(username="staff", password="password")

        fake_img = SimpleUploadedFile("scan.jpg", b"fake image bytes", content_type="image/jpeg")

        with patch("recognition.views.process_kiosk_frame") as mock_proc:
            mock_proc.return_value = (dummy_vector, "approved-onnx-model", {"is_real": True, "score": 0.95})

            response = self.client.post(
                reverse("recognition:kiosk"),
                {"image": fake_img, "format": "json"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["status"], "matched")
            self.assertEqual(data["student"]["student_id"], "STU-001")

        # Verify audit log was recorded as 'matched' automatically
        log = LookupAuditLog.objects.filter(student=self.student).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.outcome, "matched")

    def test_kiosk_auto_confirms_on_match_html(self):
        dummy_vector = [0.1] * 512
        FaceEmbedding.objects.create(student=self.student, vector=dummy_vector, model_name="approved-onnx-model")
        self.client.login(username="staff", password="password")

        fake_img = SimpleUploadedFile("scan.jpg", b"fake image bytes", content_type="image/jpeg")

        with patch("recognition.views.process_kiosk_frame") as mock_proc:
            mock_proc.return_value = (dummy_vector, "approved-onnx-model", {"is_real": True, "score": 0.95})

            response = self.client.post(
                reverse("recognition:kiosk"),
                {"image": fake_img}
            )
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "recognition/confirmed.html")
            self.assertContains(response, "Sok Dara")

    def test_kiosk_rejects_spoof_screen(self):
        self.client.login(username="staff", password="password")
        fake_img = SimpleUploadedFile("scan.jpg", b"fake screen image", content_type="image/jpeg")

        from recognition.services import FaceRecognitionUnavailable

        with patch("recognition.views.process_kiosk_frame") as mock_proc:
            mock_proc.side_effect = FaceRecognitionUnavailable("Spoof detected: Screen grid/moiré pattern detected. Please present a real live face.")

            response = self.client.post(
                reverse("recognition:kiosk"),
                {"image": fake_img, "format": "json"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertFalse(data["success"])
            self.assertEqual(data["status"], "spoof")
            self.assertIn("Spoof detected", data["error"])
