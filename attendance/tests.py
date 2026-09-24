import datetime
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from students.models import Student
from .models import AlertLog, AttendanceRecord, ClassRoom, Session, StudentFace, Teacher
from .tasks import send_absence_alerts_for_session


class SmartAttendanceTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Users
        self.teacher_user = User.objects.create_user(username="teacher1", password="password123")
        self.teacher = Teacher.objects.create(user=self.teacher_user, name="Professor Smith", email="smith@school.edu")

        self.student_user = User.objects.create_user(username="student1", password="password123")
        self.classroom = ClassRoom.objects.create(name="CS101", teacher=self.teacher)

        self.student = Student.objects.create(
            user=self.student_user,
            student_id="STU001",
            full_name="Alice Johnson",
            class_room=self.classroom,
            guardian_contact="alice_parent@example.com",
            is_active=True,
            consent_given_at=timezone.now(),
        )

        self.student2 = Student.objects.create(
            student_id="STU002",
            full_name="Bob Brown",
            class_room=self.classroom,
            guardian_contact="123456789",  # Telegram chat ID format
            is_active=True,
            consent_given_at=timezone.now(),
        )

        # Today's Session
        now = timezone.localtime()
        self.session = Session.objects.create(
            class_room=self.classroom,
            date=now.date(),
            start_time=now.time(),
            end_time=(now + datetime.timedelta(hours=1)).time(),
            qr_token="valid_qr_test_token",
            qr_token_expires_at=now + datetime.timedelta(minutes=15),
        )

    # ------------------------------------------------------------------
    # Phase 1: Models & Auth
    # ------------------------------------------------------------------
    def test_login_and_student_profile(self):
        # Login as student
        response = self.client.post("/api/auth/login/", {"username": "student1", "password": "password123"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = response.data["token"]
        self.assertEqual(response.data["role"], "student")
        self.assertEqual(response.data["student"]["student_id"], "STU001")

        # Get profile
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        me_resp = self.client.get("/api/students/me/")
        self.assertEqual(me_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(me_resp.data["student_id"], "STU001")
        self.assertEqual(me_resp.data["class_room"]["name"], "CS101")

    def test_teacher_login(self):
        response = self.client.post("/api/auth/login/", {"username": "teacher1", "password": "password123"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], "teacher")
        self.assertEqual(response.data["teacher"]["name"], "Professor Smith")

    # ------------------------------------------------------------------
    # Phase 2: QR Check-In & History
    # ------------------------------------------------------------------
    def test_qr_checkin_flow(self):
        self.client.force_authenticate(user=self.student_user)

        # Successful QR check-in
        checkin_resp = self.client.post("/api/attendance/checkin/qr/", {"qr_token": "valid_qr_test_token"})
        self.assertEqual(checkin_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(checkin_resp.data["record"]["status"], "present")

        # Re-checkin should be idempotent and return 200
        checkin_resp2 = self.client.post("/api/attendance/checkin/qr/", {"qr_token": "valid_qr_test_token"})
        self.assertEqual(checkin_resp2.status_code, status.HTTP_200_OK)

        # Check student attendance history
        history_resp = self.client.get("/api/attendance/history/")
        self.assertEqual(history_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(history_resp.data), 1)
        self.assertEqual(history_resp.data[0]["method"], "qr")

    def test_expired_qr_code(self):
        self.session.qr_token_expires_at = timezone.now() - datetime.timedelta(minutes=1)
        self.session.save()

        self.client.force_authenticate(user=self.student_user)
        resp = self.client.post("/api/attendance/checkin/qr/", {"qr_token": "valid_qr_test_token"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expired", resp.data["error"])

    def test_teacher_rotate_qr(self):
        self.client.force_authenticate(user=self.teacher_user)
        rotate_resp = self.client.post(f"/api/teacher/sessions/{self.session.id}/qr/", {"expiry_minutes": 5})
        self.assertEqual(rotate_resp.status_code, status.HTTP_200_OK)
        self.assertNotEqual(rotate_resp.data["qr_token"], "valid_qr_test_token")
        self.session.refresh_from_db()
        self.assertEqual(self.session.qr_token, rotate_resp.data["qr_token"])

    # ------------------------------------------------------------------
    # Phase 3: Absence Alerts & Idempotency
    # ------------------------------------------------------------------
    @patch("attendance.tasks.send_mail")
    @patch("attendance.tasks.send_telegram_alert")
    def test_absence_alert_task(self, mock_tg, mock_email):
        # Alice Johnson checks in
        AttendanceRecord.objects.create(student=self.student, session=self.session, status="present", method="qr")

        # Bob Brown (student2) has NOT checked in
        result = send_absence_alerts_for_session(self.session.id)
        self.assertEqual(result["sent_count"] + result["failed_count"], 1)

        # Verify Bob was recorded absent
        bob_record = AttendanceRecord.objects.get(student=self.student2, session=self.session)
        self.assertEqual(bob_record.status, "absent")

        # Verify AlertLog was created
        alert = AlertLog.objects.get(student=self.student2, session=self.session)
        self.assertIn(alert.status, ["sent", "failed"])

        # Re-running task should be idempotent (no duplicate sends)
        result2 = send_absence_alerts_for_session(self.session.id)
        self.assertEqual(result2["sent_count"], 0)

    # ------------------------------------------------------------------
    # Phase 4: Face Recognition (StudentFace & Matching)
    # ------------------------------------------------------------------
    def test_face_checkin_matching(self):
        dummy_vector = [0.1] * 512
        StudentFace.objects.create(student=self.student, embedding=dummy_vector)

        self.client.force_authenticate(user=self.teacher_user)

        with patch("recognition.services.process_kiosk_frame") as mock_frame:
            # Mock frame liveness and embedding matching dummy_vector
            mock_frame.return_value = (dummy_vector, "approved-onnx-model", {"is_real": True, "score": 0.98, "details": "Real face verified", "metrics": {}})

            from django.core.files.uploadedfile import SimpleUploadedFile
            fake_img = SimpleUploadedFile("face.jpg", b"fake image bytes", content_type="image/jpeg")

            resp = self.client.post(
                "/api/attendance/checkin/face/",
                {"image": fake_img, "session_id": self.session.id},
                format="multipart"
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertTrue(resp.data["matched"])
            self.assertEqual(resp.data["student_id"], "STU001")

    def test_face_checkin_low_confidence_rejection(self):
        dummy_vector = [0.1] * 512
        diff_vector = [-0.1] * 512
        StudentFace.objects.create(student=self.student, embedding=dummy_vector)

        self.client.force_authenticate(user=self.teacher_user)

        with patch("recognition.services.process_kiosk_frame") as mock_frame:
            mock_frame.return_value = (diff_vector, "approved-onnx-model", {"is_real": True, "score": 0.95, "details": "Real face verified", "metrics": {}})
            from django.core.files.uploadedfile import SimpleUploadedFile
            fake_img = SimpleUploadedFile("face.jpg", b"fake image bytes", content_type="image/jpeg")

            resp = self.client.post(
                "/api/attendance/checkin/face/",
                {"image": fake_img, "session_id": self.session.id},
                format="multipart"
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertFalse(resp.data["matched"])
            self.assertIn("not recognized", resp.data["message"])

    def test_face_checkin_spoof_rejection(self):
        from recognition.services import FaceRecognitionUnavailable
        self.client.force_authenticate(user=self.teacher_user)

        with patch("recognition.services.process_kiosk_frame") as mock_frame:
            mock_frame.side_effect = FaceRecognitionUnavailable("Spoof detected: Planar 2D surface detected (flat photo/screen).")
            from django.core.files.uploadedfile import SimpleUploadedFile
            fake_img = SimpleUploadedFile("photo_attack.jpg", b"fake flat photo bytes", content_type="image/jpeg")

            resp = self.client.post(
                "/api/attendance/checkin/face/",
                {"image": fake_img, "session_id": self.session.id},
                format="multipart"
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertTrue(resp.data.get("is_spoof"))
            self.assertIn("Spoof detected", resp.data.get("error", ""))

    def test_dynamic_qr_checkin_and_expiry(self):
        from attendance.security import generate_dynamic_qr_token
        self.client.force_authenticate(user=self.student_user)

        # 1. Valid current window dynamic token
        valid_dynamic_token = generate_dynamic_qr_token(self.session.id, window_offset=0)
        resp = self.client.post("/api/attendance/checkin/qr/", {"qr_token": valid_dynamic_token})
        self.assertIn(resp.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

        # 2. Expired dynamic token (window offset -5)
        expired_dynamic_token = generate_dynamic_qr_token(self.session.id, window_offset=-5)
        bad_resp = self.client.post("/api/attendance/checkin/qr/", {"qr_token": expired_dynamic_token})
        self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Dynamic QR code has expired", bad_resp.data.get("error", ""))

    def test_campus_subnet_restriction(self):
        self.client.force_authenticate(user=self.student_user)
        valid_token = "valid_qr_test_token"

        # Restrict allowed subnets to 10.0.0.0/8
        with self.settings(ATTENDANCE_ALLOWED_SUBNETS=["10.0.0.0/8"]):
            # Request from home IP 203.0.113.19 should be blocked
            resp = self.client.post(
                "/api/attendance/checkin/qr/",
                {"qr_token": valid_token},
                REMOTE_ADDR="203.0.113.19"
            )
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
            self.assertIn("restricted to the authorized campus network", resp.data.get("error", ""))

            # Request from campus Wi-Fi 10.5.12.33 should be permitted
            resp_allowed = self.client.post(
                "/api/attendance/checkin/qr/",
                {"qr_token": valid_token},
                REMOTE_ADDR="10.5.12.33"
            )
            self.assertIn(resp_allowed.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

    # ------------------------------------------------------------------
    # Phase 5 & 6: Reports, Overrides, and Audits
    # ------------------------------------------------------------------
    def test_teacher_manual_override(self):
        record = AttendanceRecord.objects.create(
            student=self.student,
            session=self.session,
            status="absent",
            method="manual"
        )
        self.client.force_authenticate(user=self.teacher_user)

        patch_resp = self.client.patch(
            f"/api/teacher/attendance/{record.id}/",
            {"status": "present"}
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.assertEqual(record.status, "present")
        self.assertEqual(record.edited_by, self.teacher_user)

    def test_reports_api(self):
        AttendanceRecord.objects.create(student=self.student, session=self.session, status="present", method="qr")
        AttendanceRecord.objects.create(student=self.student2, session=self.session, status="absent", method="manual")

        self.client.force_authenticate(user=self.teacher_user)

        # Class report
        class_rep = self.client.get(f"/api/reports/class/{self.classroom.id}/")
        self.assertEqual(class_rep.status_code, status.HTTP_200_OK)
        self.assertEqual(class_rep.data["total_attendance_records"], 2)
        self.assertEqual(class_rep.data["present_count"], 1)
        self.assertEqual(class_rep.data["absent_count"], 1)

        # Student report
        stu_rep = self.client.get(f"/api/reports/student/{self.student.id}/")
        self.assertEqual(stu_rep.status_code, status.HTTP_200_OK)
        self.assertEqual(stu_rep.data["present_count"], 1)
        self.assertEqual(stu_rep.data["attendance_rate"], 100.0)

    def test_logout(self):
        token, _ = Token.objects.get_or_create(user=self.student_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        logout_resp = self.client.post("/api/auth/logout/")
        self.assertEqual(logout_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(user=self.student_user).exists())

        # Next request with same token should be unauthorized
        profile_resp = self.client.get("/api/students/me/")
        self.assertEqual(profile_resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password(self):
        token, _ = Token.objects.get_or_create(user=self.student_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        # Wrong old password
        bad_resp = self.client.post("/api/auth/change-password/", {
            "old_password": "wrong-password",
            "new_password": "new-secret-password-123",
            "confirm_password": "new-secret-password-123",
        })
        self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Successful change
        ok_resp = self.client.post("/api/auth/change-password/", {
            "old_password": "password123",
            "new_password": "new-secret-password-123",
            "confirm_password": "new-secret-password-123",
        })
        self.assertEqual(ok_resp.status_code, status.HTTP_200_OK)
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.check_password("new-secret-password-123"))

    def test_teacher_session_create(self):
        self.client.force_authenticate(user=self.teacher_user)
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        resp = self.client.post("/api/teacher/sessions/", {
            "class_room": self.classroom.id,
            "date": str(tomorrow),
            "start_time": "14:00:00",
            "end_time": "16:00:00",
            "auto_generate_qr": True,
            "qr_expiry_minutes": 60,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNotNone(resp.data["qr_token"])
        self.assertTrue(resp.data["is_qr_valid"])

    def test_teacher_classroom_list_and_create(self):
        self.client.force_authenticate(user=self.teacher_user)
        # 1. List classrooms
        resp = self.client.get("/api/teacher/classrooms/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(c["id"] == self.classroom.id for c in resp.data))

        # 2. Create new classroom
        resp = self.client.post("/api/teacher/classrooms/", {"name": "Robotics & AI Lab"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["name"], "Robotics & AI Lab")
        self.assertEqual(resp.data["teacher"]["id"], self.teacher.id)

        # 3. Unauthorized student cannot create
        self.client.force_authenticate(user=self.student_user)
        resp = self.client.post("/api/teacher/classrooms/", {"name": "Hacking 101"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_session_roster(self):
        AttendanceRecord.objects.create(student=self.student, session=self.session, status="present", method="qr")
        self.client.force_authenticate(user=self.teacher_user)

        resp = self.client.get(f"/api/teacher/sessions/{self.session.id}/roster/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["summary"]["present"], 1)
        self.assertEqual(resp.data["summary"]["unmarked"], 1)
        self.assertEqual(len(resp.data["roster"]), 2)

    def test_filtered_attendance_history(self):
        AttendanceRecord.objects.create(student=self.student, session=self.session, status="present", method="qr")
        self.client.force_authenticate(user=self.student_user)

        # Filter by status=present
        resp = self.client.get("/api/attendance/history/?status=present")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

        # Filter by status=absent -> should be empty
        resp2 = self.client.get("/api/attendance/history/?status=absent")
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp2.data), 0)

    def test_api_root_and_health(self):
        root_resp = self.client.get("/api/")
        self.assertEqual(root_resp.status_code, status.HTTP_200_OK)
        self.assertIn("endpoints", root_resp.data)

        health_resp = self.client.get("/api/health/")
        self.assertIn(health_resp.status_code, [status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE])
        self.assertIn("services", health_resp.data)

    def test_api_docs_and_schema(self):
        schema_resp = self.client.get("/api/schema/")
        self.assertEqual(schema_resp.status_code, 200)

        docs_resp = self.client.get("/api/docs/")
        self.assertEqual(docs_resp.status_code, 200)

    def test_student_today_schedule(self):
        self.client.force_authenticate(user=self.student_user)
        resp = self.client.get("/api/students/schedule/today/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["id"], self.session.id)
        self.assertIn("is_qr_active", resp.data[0])
        self.assertIn("is_checked_in", resp.data[0])

    def test_student_alerts_mine(self):
        AlertLog.objects.create(
            student=self.student,
            session=self.session,
            channel="telegram",
            status="sent",
        )
        self.client.force_authenticate(user=self.student_user)
        resp = self.client.get("/api/alerts/mine/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["channel"], "telegram")
        self.assertEqual(resp.data[0]["status"], "sent")

    def test_teacher_session_bulk_attendance(self):
        self.client.force_authenticate(user=self.teacher_user)
        resp = self.client.post(
            f"/api/teacher/sessions/{self.session.id}/attendance/bulk/",
            {
                "records": [
                    {"student_id": self.student.student_id, "status": "present"},
                    {"student_id": self.student2.student_id, "status": "absent"},
                ]
            },
            format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["updated_count"], 2)

        # Verify in DB
        r1 = AttendanceRecord.objects.get(student=self.student, session=self.session)
        r2 = AttendanceRecord.objects.get(student=self.student2, session=self.session)
        self.assertEqual(r1.status, "present")
        self.assertEqual(r2.status, "absent")

    def test_class_report_export_csv(self):
        AttendanceRecord.objects.create(
            student=self.student,
            session=self.session,
            status="present",
            method="face",
            confidence_score=0.92,
        )
        self.client.force_authenticate(user=self.teacher_user)
        resp = self.client.get(f"/api/reports/class/{self.classroom.id}/export-csv/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", resp["Content-Disposition"])
        content = resp.content.decode("utf-8")
        self.assertIn("Session Date,Classroom,Session Time", content)
        self.assertIn(self.student.student_id, content)
        self.assertIn("Present", content)
        self.assertIn("Face Recognition", content)

    def test_multi_angle_best_match(self):
        from recognition.services import best_match
        dummy_vector_front = [0.1] * 512
        dummy_vector_profile = [0.8] * 512

        # Create multi-angle StudentFace embeddings
        StudentFace.objects.create(student=self.student, embedding=dummy_vector_front)
        StudentFace.objects.create(student=self.student, embedding=dummy_vector_profile)

        # Test matching against side profile vector
        matched_student, score = best_match(dummy_vector_profile)
        self.assertIsNotNone(matched_student)
        self.assertEqual(matched_student.student_id, self.student.student_id)
        self.assertGreater(score, 0.99)

        # Test classroom filtering
        other_room = ClassRoom.objects.create(name="Room 999")
        empty_student, score2 = best_match(dummy_vector_profile, class_room=other_room)
        self.assertIsNone(empty_student)

    def test_teacher_session_live_feed(self):
        url = f"/api/teacher/sessions/{self.session.id}/live-feed/"
        self.client.force_authenticate(user=self.teacher_user)

        # Create an attendance record
        AttendanceRecord.objects.create(
            student=self.student,
            session=self.session,
            status="present",
            method="face",
            confidence_score=0.94
        )

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["session_id"], self.session.id)
        self.assertEqual(data["present_count"], 1)
        self.assertEqual(data["checked_in_count"], 1)
        self.assertEqual(len(data["recent_checkins"]), 1)
        self.assertEqual(data["recent_checkins"][0]["student_id"], self.student.student_id)
        self.assertEqual(data["recent_checkins"][0]["status"], "present")

    def test_kiosk_rate_limiter(self):
        from django.test import RequestFactory
        from recognition.views import check_kiosk_rate_limit
        from django.core.cache import cache

        cache.clear()
        factory = RequestFactory()
        req = factory.post("/", REMOTE_ADDR="198.51.100.25")

        # Within limit
        limited, ip = check_kiosk_rate_limit(req, max_requests=3, window_secs=30)
        self.assertFalse(limited)
        self.assertEqual(ip, "198.51.100.25")

        check_kiosk_rate_limit(req, max_requests=3, window_secs=30)
        check_kiosk_rate_limit(req, max_requests=3, window_secs=30)

        # 4th request exceeds limit of 3
        limited_4th, _ = check_kiosk_rate_limit(req, max_requests=3, window_secs=30)
        self.assertTrue(limited_4th)

