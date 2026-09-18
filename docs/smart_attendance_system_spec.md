# Smart Attendance System — Build Spec (Django)

## Context for the AI assistant

This is a school/university attendance system built by a 5-person student team.
There is an **existing Django project** (already running in Docker) that has
**antelopev2 (InsightFace)** set up for face recognition. Before generating
new code, inspect the existing repo structure, `docker-compose.yml`,
`requirements.txt`/`pyproject.toml`, and any existing face-recognition
integration code, and match this spec's models/endpoints to what already
exists rather than assuming a fresh project layout.

Build incrementally in the phase order below — do not implement later phases
before earlier ones are working and testable.

---

## 1. Project Overview

**Name:** Smart Attendance System

**Purpose:** Digital attendance tracking for a school/university via QR-code
check-in and face-recognition check-in, with automatic absence alerts,
attendance reports, and student monitoring.

**Scope for v1:** Single institution (not multi-tenant). Multi-school support,
offline check-in, and liveness detection are explicitly out of scope for v1.

---

## 2. Team & Roles

| Name | Role |
|---|---|
| Meas Vanny | Backend Developer |
| Sat Chhumseak | Backend Developer |
| Nov Thearith | Mobile Developer (Flutter) |
| Chhom Rosvisal | UI/UX Designer |
| Sem Sreyneat | Marketing and Testing |

---

## 3. Tech Stack

- **Backend:** Django + Django REST Framework (existing project)
- **Face recognition:** antelopev2 / InsightFace (already integrated — reuse existing service/module, do not reintroduce a separate microservice)
- **Database:** PostgreSQL (or whatever the existing Django project already uses — check `settings.py`)
- **Async jobs:** Celery + Redis (for absence-alert jobs and any batch face-embedding work)
- **Admin panel:** Django Admin (extend with `django-unfold` or `django-admin-interface` if not already present, for a cleaner UI)
- **Mobile apps:** Flutter (Student app, Teacher app) consuming the DRF API
- **Alerts:** Telegram Bot API and/or email (SMTP)
- **Containerization:** Docker Compose (existing) — extend, don't replace

---

## 4. Core Data Models

Adapt field names/types to match any existing models in the repo. If these
models don't exist yet, create them in a new Django app (suggested name:
`attendance`), alongside existing apps.

```python
# accounts / existing user app
class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    student_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    class_room = models.ForeignKey("ClassRoom", on_delete=models.SET_NULL, null=True)
    guardian_contact = models.CharField(max_length=100, blank=True)  # phone or Telegram chat_id
    is_active = models.BooleanField(default=True)

class Teacher(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=255)
    email = models.EmailField()

class ClassRoom(models.Model):
    name = models.CharField(max_length=100)
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True)

class Session(models.Model):
    class_room = models.ForeignKey(ClassRoom, on_delete=models.CASCADE)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    qr_token = models.CharField(max_length=128, unique=True, null=True, blank=True)
    qr_token_expires_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

class AttendanceRecord(models.Model):
    STATUS_CHOICES = [("present", "Present"), ("late", "Late"), ("absent", "Absent")]
    METHOD_CHOICES = [("qr", "QR Code"), ("face", "Face Recognition"), ("manual", "Manual")]
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    checked_in_at = models.DateTimeField(auto_now_add=True)
    confidence_score = models.FloatField(null=True, blank=True)  # for face matches
    is_deleted = models.BooleanField(default=False)  # soft delete / audit trail
    edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ("student", "session")

class StudentFace(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="face_embeddings")
    embedding = models.JSONField()  # or a vector field if pgvector is available
    created_at = models.DateTimeField(auto_now_add=True)

class AlertLog(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    channel = models.CharField(max_length=20)  # telegram / email
    status = models.CharField(max_length=20)  # sent / failed
    sent_at = models.DateTimeField(auto_now_add=True)
    error_message = models.TextField(blank=True)
```

---

## 5. API Endpoints (DRF)

```
POST   /api/auth/login/                     — token auth (student/teacher)
GET    /api/students/me/                    — current student profile

POST   /api/face/enroll/                    — upload photo(s), generate + store embedding
POST   /api/attendance/checkin/qr/          — { qr_token } -> mark attendance
POST   /api/attendance/checkin/face/        — { image } -> match embedding -> mark attendance
GET    /api/attendance/history/             — student's own attendance records

GET    /api/teacher/classes/today/          — teacher's sessions today
POST   /api/teacher/sessions/{id}/qr/       — generate/rotate QR token for a session
POST   /api/teacher/sessions/{id}/end/      — end session -> triggers absence-alert task
PATCH  /api/teacher/attendance/{id}/        — manual override (status change), sets edited_by

GET    /api/reports/class/{id}/             — attendance stats for a class
GET    /api/reports/student/{id}/           — attendance history for one student
```

---

## 6. Face Recognition Integration

- Reuse the existing antelopev2/InsightFace setup already in the Docker project — do not add a second face-recognition dependency.
- **Enrollment:** capture 3–5 images per student at different angles → generate embeddings → store in `StudentFace`.
- **Matching:** on check-in, generate embedding from the live capture, compare via cosine similarity against the student's stored embeddings (and, if performance allows, all active students in that session's class — not the whole school — to keep the search space small).
- **Threshold:** start with cosine similarity ≥ 0.5 (tune based on real testing — flag this as a config value, not a hardcoded constant).
- **Low-confidence matches:** if below threshold, do not auto-mark attendance — return a "not recognized, try QR or ask teacher" response instead of guessing.
- **No liveness detection in v1** — note this as a known limitation in the code (e.g., a code comment and a TODO), not a silent gap.

---

## 7. Absence Alerts (Celery)

- Triggered by `POST /api/teacher/sessions/{id}/end/`.
- Celery task: for each active student in the class with no `AttendanceRecord` for that session, create an `AlertLog` and send via Telegram Bot API (fallback: email if no Telegram contact on file).
- Wrap the send in try/except; on failure, mark `AlertLog.status = "failed"` with the error message rather than losing the job silently (addresses the "no dead-letter handling" gap identified earlier).
- Make this task idempotent — re-running it for the same session should not double-send alerts for students already logged.

---

## 8. Known Gaps to Design Around (from earlier review)

Build with these in mind, even if some are deferred:
- QR token should have an expiry (`qr_token_expires_at`) and be rotatable per session — not a single shared static code.
- Define a clear late-vs-present threshold (e.g., checked in more than N minutes after `start_time` = late) as a configurable setting.
- `AttendanceRecord` uses soft-delete (`is_deleted`) and tracks `edited_by` for any manual override — never hard-delete attendance data.
- Offline check-in and device/location verification are out of scope for v1 — do not build partial/half-working versions of these; leave clean extension points instead (e.g., a `method` field that already anticipates a future `"offline_sync"` value).

---

## 9. Build Order (do not skip ahead)

1. **Phase 1 — Core CRUD:** Student, Teacher, ClassRoom, Session, AttendanceRecord models + Django Admin registration + basic DRF read endpoints. Get this fully working and manually testable before touching face recognition.
2. **Phase 2 — QR check-in:** token generation/expiry on Session, `checkin/qr/` endpoint, attendance history endpoint.
3. **Phase 3 — Absence alerts:** Celery + Redis wiring, `sessions/{id}/end/` endpoint, Telegram/email sending, AlertLog.
4. **Phase 4 — Face recognition:** StudentFace model, enrollment endpoint, `checkin/face/` endpoint using the existing antelopev2 integration.
5. **Phase 5 — Reports:** class/student report endpoints, basic aggregation queries.
6. **Phase 6 — Polish:** manual override endpoint with audit fields, admin UI cleanup, error states, edge cases from Section 8.

---

## 10. Out of Scope for v1 (confirm before building)

- Multi-tenant (multi-school) support
- Offline attendance sync
- Liveness detection / anti-spoofing for face recognition
- SMS alerts (Telegram/email only)
