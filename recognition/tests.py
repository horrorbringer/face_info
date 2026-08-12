from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class KioskTests(TestCase):
    def test_kiosk_requires_staff_login(self):
        self.assertEqual(self.client.get(reverse("recognition:kiosk")).status_code, 302)

    def test_unconfigured_recognition_explains_manual_fallback(self):
        User.objects.create_user("staff", password="password")
        self.client.login(username="staff", password="password")
        response = self.client.post(reverse("recognition:kiosk"), {})
        self.assertContains(response, "Capture a camera frame first.")
