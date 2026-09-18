# Smart Attendance System — Testing & QA Guide

This guide is for QA, testing, and team members validating the Smart Attendance System across development, staging, and demo environments.

---

## 1. Automated Test Suite

We provide an automated test suite covering authentication, QR validation, absence alerts, face matching, teacher overrides, and reports.

### Run in Docker
```bash
docker compose exec web python manage.py test attendance students
```

### Run Locally
```bash
python manage.py test attendance students
```

**Expected Result:**
```text
Found 13 test(s).
Creating test database for alias 'default'...
.............
Ran 13 tests in ~50s
OK
Destroying test database for alias 'default'...
```

---

## 2. Manual Testing Checklist

| Test ID | Feature | Test Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| **TC-01** | Student Login | POST `/api/auth/login/` with student credentials | Returns `token`, `role: "student"`, and student profile |
| **TC-02** | Teacher Login | POST `/api/auth/login/` with teacher credentials | Returns `token`, `role: "teacher"`, and teacher profile |
| **TC-03** | QR Generation | Teacher calls `POST /api/teacher/sessions/{id}/qr/` | Returns new `qr_token` and future `qr_token_expires_at` timestamp |
| **TC-04** | Valid QR Check-in | Student scans QR before expiry within 15 min of class start | Returns `201 Created` with `status: "present"` |
| **TC-05** | Late QR Check-in | Student scans QR >15 min after session start time | Returns `201 Created` with `status: "late"` |
| **TC-06** | Expired QR Check-in | Student scans QR after `qr_token_expires_at` | Returns `400 Bad Request` with message "QR code has expired" |
| **TC-07** | Duplicate Check-in | Student scans QR a second time for same session | Returns `200 OK` with "Already checked in" |
| **TC-08** | Wrong Class Check-in| Student from Class B scans QR from Class A | Returns `403 Forbidden` with enrollment warning |
| **TC-09** | Session End & Alerts | Teacher calls `POST /api/teacher/sessions/{id}/end/` | Unchecked students marked `absent`; Celery alerts dispatched |
| **TC-10** | Alert Idempotency | End session called twice | Does not duplicate alerts for already-sent students |
| **TC-11** | Manual Override | Teacher modifies record to "present" | Status updates, `edited_by` recorded, soft-delete works |
| **TC-12** | Class Report | GET `/api/reports/class/{id}/` | Correctly computes counts and percentages |

---

## 3. Verifying Background Tasks (Celery & Redis)

1. **Check worker process health:**
   ```bash
   docker compose logs celery
   ```
   Look for: `celery@... ready.` and `[tasks] . attendance.tasks.send_absence_alerts_for_session`.

2. **Verify Alert Audit Logs in Admin:**
   - Log into `http://localhost:8000/admin/`.
   - Open **Alert Logs**.
   - Check that each student who was absent has an entry with status `sent` (or `failed` if token is unconfigured), channel, and exact timestamp.

---

## 4. Resetting Test Data

To reset the database cleanly for testing:
```bash
# In Docker:
docker compose down -v
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```
