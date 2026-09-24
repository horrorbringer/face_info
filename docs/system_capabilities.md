# System Features & Capabilities Specification

This document provides a comprehensive, production-grade reference for all features, operational capabilities, architectural designs, and security mechanisms implemented in the **Smart Attendance & Face Recognition System**.

---

## 🏗️ 1. Architecture & Technology Stack

The platform is designed as an asynchronous, containerized, micro-service-ready architecture supporting mobile devices, web terminals, and dedicated gate kiosks.

```text
               ┌────────────────────────────────────────────────────────┐
               │                     Client Layer                       │
               │   • Flutter Mobile App (Students & Teachers)           │
               │   • Web Kiosk Station (Entrance Biometric Camera)      │
               │   • Staff Web Portal & Django Admin                    │
               └───────────────────────────┬────────────────────────────┘
                                           │ HTTPS (Port 443)
                                           ▼
               ┌────────────────────────────────────────────────────────┐
               │           Nginx SSL Reverse Proxy & Static Host        │
               │   • Let's Encrypt / Cloudflare SSL Termination         │
               │   • Upstream Buffering & 120s Read Timeout             │
               └───────────────────────────┬────────────────────────────┘
                                           │ HTTP/1.1 (Port 8000)
                                           ▼
               ┌────────────────────────────────────────────────────────┐
               │          Django 5.0 REST Backend (Gunicorn gthread)     │
               │   • 2 Workers, 4 Threads per Worker (Memory-Efficient) │
               │   • Connection Pooling (CONN_MAX_AGE = 60s)            │
               │   • InsightFace Biometric Engine (512D Embeddings)     │
               └──────────────┬──────────────────────────┬──────────────┘
                              │                          │
                              ▼                          ▼
               ┌────────────────────────┐      ┌────────────────────────┐
               │   PostgreSQL 16 DB     │      │   Redis 7 In-Memory    │
               │   (pgvector Enabled)   │      │   • Celery Task Broker │
               │   • Relational Schema  │      │   • Device Anti-Hopping│
               │   • Composite Indexes  │      │   • Cache Layer        │
               └────────────────────────┘      └───────────┬────────────┘
                                                           │
                                                           ▼
                                               ┌────────────────────────┐
                                               │ Celery & Celery Beat   │
                                               │   • Absence Dispatch   │
                                               │   • Telegram / Email   │
                                               │   • Lifecycle Manager  │
                                               └────────────────────────┘
```

---

## 👥 2. Authentication, Roles & Multi-Teacher Authorization

### 2.1 Multi-Mode Authentication
* **Token Authentication:** Cryptographic DRF tokens returned via `POST /api/auth/login/` for Flutter mobile apps and API clients.
* **Flexible Authorization Header Support:** Supports both `Authorization: Token <key>` and standard `Authorization: Bearer <key>`.
* **Session Authentication:** Django cookie-based sessions for the staff web portal and administration dashboard.
* **Graceful Revocation:** `POST /api/auth/logout/` invalidates the active user token immediately.

### 2.2 Role-Based Access Control (RBAC)
1. **Student:** Access limited to personal schedule (`/api/students/schedule/today/`), personal profile (`/api/students/me/`), check-in history (`/api/attendance/history/`), and check-in endpoints.
2. **Teacher (Primary):** Owns classrooms, schedules sessions, generates dynamic QR codes, monitors real-time rosters, overrides attendance, and exports class reports.
3. **Co-Teacher (Assistant / Substitute):**
   * Configured via `ClassRoom.co_teachers = ManyToManyField(Teacher)`.
   * Authorized to manage classrooms, start/end sessions, generate rotating QR codes, inspect live rosters, and edit attendance records seamlessly if the primary teacher is unavailable.
4. **Staff / Administrator:** Full global access to all classrooms, audit logs, student rosters, biometric revocations, and system health monitors.

---

## 🏫 3. Student Enrollment & Multi-Classroom Architecture

### 3.1 Multi-Classroom Student Enrollment
Students are no longer restricted to a single class:
* **Many-to-Many Enrollment:** Model field `Student.classrooms = ManyToManyField("attendance.ClassRoom")` enables a student to attend courses across multiple departments, lab sections, or grades simultaneously.
* **Backward Compatibility:** Preserves legacy `Student.class_room` foreign key as a primary classroom fallback.
* **Unified Enrollment Helpers:**
  * `student.get_enrolled_classrooms()`: Queries all classrooms linked to the student across both relationship types.
  * `student.is_enrolled_in(classroom)`: O(1) cached membership check preventing unauthorized attendance scans.
  * `classroom.get_enrolled_students(active_only=True)`: Resolves the complete student roster for any class.

### 3.2 Teacher Classroom Student Management API
Teachers can manage course enrollments directly from mobile or web without requiring staff superadmin intervention:
* **List Enrolled Students:** `GET /api/teacher/classrooms/<id>/students/`
* **Enroll Student by ID:** `POST /api/teacher/classrooms/<id>/students/` with payload `{"student_id": "STU1001"}`.
* **Unenroll Student:** `DELETE /api/teacher/classrooms/<id>/students/` with payload `{"student_id": "STU1001"}`.

### 3.3 Bulk Student Roster Import
* **Endpoint / Portal:** `/students/import/`
* Accepts CSV files containing `student_id, full_name, class_year, guardian_contact, guardian_email, guardian_phone, guardian_telegram_id` to register entire cohorts in seconds.

---

## 📸 4. Attendance Check-In Subsystems

### 4.1 Method A: Dynamic Rotating QR Code (Anti-Cheat & Projector View)
* **Endpoint:** `POST /api/attendance/checkin/qr/`
* **Payload:** `{ "qr_token": "dyn_<session_id>_<hmac_token>", "device_id": "<uuid>" }`
* **Features:**
  1. **TOTP-Style Cryptographic Tokens:** Dynamic tokens rotate every 20 seconds using HMAC-SHA256 based on the session ID, secret key, and current time step.
  2. **Classroom Projector Mode (`/teacher/sessions/<id>/live-qr/`):** Fullscreen responsive web interface designed to project the live QR code on classroom display boards with a live countdown ring.
  3. **Anti-Replay Window:** Validates token against current and previous time windows to allow for minor network latency while rejecting stale photos taken by absent students.
  4. **Dynamic Expiration & Fallback:** Supports static 32-byte URL-safe tokens with explicit expiration timestamps (`SessionRotateQRView`).

### 4.2 Method B: Biometric Face Recognition
* **Endpoint:** `POST /api/attendance/checkin/face/`
* **Payload:** Multipart form data (`image: <camera_frame>`, `session_id: <id>`, `device_id: <uuid>`)
* **Features:**
  1. **SIMD & Vectorized Cosine Matching:** Normalized 512-dimensional facial embeddings extracted using InsightFace (ArcFace / MobileFaceNet ONNX).
  2. **Bounded Search Scope:** Pre-filters student candidates strictly to students enrolled in that specific session's classroom, keeping matching times under **10 milliseconds**.
  3. **Automated Frame Normalization:** Incoming camera frames exceeding 1280px are automatically downscaled before ONNX inference, preventing memory bloat and Gunicorn OOM worker crashes.
  4. **Confidence Thresholding:** Validated against `MATCH_THRESHOLD = 0.5`. Low-confidence matches are rejected with guidance to use QR code or request teacher manual check-in.
  5. **Zero Frame Persistence:** Raw camera frames are processed entirely in ephemeral RAM and never saved to disk or database.

### 4.3 Method C: Manual Overrides & Bulk Marking
* **Individual Override:** `PATCH /api/teacher/attendance/<record_id>/`
  * Payload: `{"status": "present" | "late" | "absent", "is_deleted": false}`
  * Automatically stamps `edited_by = request.user` for regulatory auditability.
* **Bulk Attendance Marker:** `POST /api/teacher/sessions/<session_id>/attendance/bulk/`
  * Allows teachers to mark the entire class present or absent with a single request.

### 4.4 Method D: Unattended Entrance Kiosk Station
* **Web Portal:** Station URL `/`
* **Features:** Hands-free continuous camera scanning, multi-factor anti-spoofing heuristics (frequency moiré analysis, 3D curvature, temporal eye blink challenges), melodic chime feedback via Web Audio API, and gate check-in logging.

---

## 🛡️ 5. Anti-Cheat & Security Mechanisms

| Mechanism | Description | Mitigation |
| :--- | :--- | :--- |
| **Device Anti-Hopping** | Binds `device_id` to student ID in Redis (`session_device:<session_id>:<device_id>`) for 12 hours. | Prevents one student with multiple phones from punching in absent friends ("Buddy Punching"). |
| **Impossible Travel Guard** | Checks if student checked into another classroom located elsewhere within the last 15 minutes. | Prevents simultaneous proxy check-ins across multiple classrooms. |
| **Cloudflare IP Spoof Guard** | Uses `HTTP_CF_CONNECTING_IP` over untrusted client-supplied `X-Forwarded-For` headers. | Prevents remote students from spoofing allowed campus Wi-Fi IPs behind Cloudflare reverse proxies. |
| **Cancelled Session Guard** | Blocks rotating dynamic QR codes (`SESSION_CANCELLED`) on cancelled sessions. | Prevents teachers from accidentally accepting attendance on cancelled sessions. |
| **Multi-Class Bulk Attendance** | Validates students across both `classrooms` (M2M) and legacy `class_room`. | Ensures teachers can bulk-mark all enrolled students regardless of enrollment method. |
| **Campus Wi-Fi Whitelist** | Compares client IP against `ATTENDANCE_ALLOWED_SUBNETS` (CIDR blocks). | Prevents remote students at home from scanning QR code screenshots sent via chat apps. |
| **Rotating QR Tokens** | Re-hashes token every 20 seconds. | Renders photos taken of the classroom projector screen invalid before they can be forwarded. |

---

## ⏱️ 6. Session Lifecycle & Precision Time Tracking

### 6.1 Accurate Session Lifecycle Fields
* **`started_at` (DateTimeField):** Stamped the exact moment a teacher activates or rotates the session QR code or projects the live screen.
* **`ended_at` (DateTimeField):** Stamped when the session is closed by the teacher or auto-closed by Celery.
* **`is_cancelled` (BooleanField):** Flags sessions cancelled due to holidays, severe weather, or teacher absence.

### 6.2 Precision Late Calculation
Eliminates all guesswork. Arrival status is calculated dynamically:
$$\text{Late Cutoff} = (\text{session.started\_at} \lor \text{session.start\_time}) + \text{LATE\_THRESHOLD\_MINUTES}$$
* Check-in time $\le \text{Late Cutoff} \implies$ **`Present`**.
* Check-in time $> \text{Late Cutoff} \implies$ **`Late`**.

### 6.3 Scheduling Overlap & Conflict Detection
When teachers create sessions via `POST /api/teacher/sessions/`:
1. **Physical Classroom Overlap:** Prevents scheduling two classes in the same physical room simultaneously.
2. **Teacher Schedule Conflict:** Prevents a teacher (or co-teacher) from being double-booked in two different classrooms at the same time.

### 6.4 Session Reopen & Cancellation
* **Reopen Grace Window:** `POST /api/teacher/sessions/<id>/reopen/` allows reopening an accidentally closed class within 30 minutes.
* **Safe Cancellation:** `POST /api/teacher/sessions/<id>/cancel/` marks the session cancelled and prevents false absence alerts from dispatching to parents.

---

## 📢 7. Automated Absence Alerts & Background Tasks

### 7.1 Automated Notification Dispatch
When a session is ended (`POST /api/teacher/sessions/<id>/end/`):
1. **Non-Blocking Hand-off:** Session status updates immediately; background dispatch is handed off to **Celery** (with automated fallback to a detached daemon thread if Celery is offline).
2. **Missing Student Detection:** All active students enrolled in the class without an existing `AttendanceRecord` are recorded with:
   * `status = "absent"`
   * `method = "system"`
   * `checked_in_at = None` (preserves data truth: absent students never checked in).
3. **Multi-Channel Dispatch:**
   * **Telegram Bot:** Formatted HTML notice dispatched via Telegram Bot API using persistent connection pooling.
   * **Email Fallback:** Sent via SMTP to `student.effective_guardian_email`.
4. **Idempotent Audit Log (`AlertLog`):** Logs channel, status (`sent` or `failed`), timestamp, and exact error message, preventing duplicate messages if tasks retry.

### 7.2 Structured Guardian Contact Model
* **`guardian_email`:** Dedicated validated email field.
* **`guardian_phone`:** Formatted phone number.
* **`guardian_telegram_id`:** Numerical Telegram chat ID.
* **Backward-Compatible Fallback:** Property getters `effective_guardian_email` and `effective_guardian_telegram_id` automatically parse legacy `guardian_contact` strings.

### 7.3 Celery Beat Lifecycle Daemon
* **Task:** `attendance.tasks.auto_manage_session_lifecycle` runs every 60 seconds.
* **Auto-Start:** Automatically activates scheduled classes when current time reaches `start_time`.
* **Auto-Close:** Automatically ends abandoned sessions when `current_time > end_time` and triggers absence alerts without teacher intervention.

---

## 📊 8. Analytics & Reporting

1. **Real-Time Polling Live Feed:**
   * Endpoint: `GET /api/teacher/sessions/<id>/live-feed/?since=<timestamp>`
   * Returns live counters: `total_enrolled`, `present_count`, `late_count`, `absent_count`, `unmarked_count`, and incremental check-in events.
2. **Classroom Aggregate Report:**
   * Endpoint: `GET /api/reports/class/<class_id>/`
   * Computes attendance rates, percentage distributions, and total sessions conducted.
3. **Official CSV Roster Export:**
   * Endpoint: `GET /api/reports/class/<class_id>/export-csv/`
   * Generates downloadable spreadsheet with complete audit logs (`Date, Class, Time, Student ID, Name, Status, Method, Checked In At, Confidence, Edited By`).
4. **Individual Student Report:**
   * Endpoint: `GET /api/reports/student/<student_id>/`
   * Accessible by the student, their assigned teachers, or staff.

---

## 🩺 9. System Health & Observability

* **Live Deep Health Check:** `GET /api/health/`
  * Checks PostgreSQL connection (`SELECT 1`).
  * Checks Redis ping.
  * Checks InsightFace model runtime status (`face_recognition: ready`).
  * Returns `HTTP 200` when healthy; returns `HTTP 503` with service diagnostics if any dependency fails.
* **API Documentation & Schema:**
  * Interactive Swagger / ReDoc: `GET /api/docs/`
  * OpenAPI 3.0 JSON Schema: `GET /api/schema/`
* **Diagnostic CLI Tools:**
  * `python manage.py test_telegram`: Verifies Telegram Bot API credentials and sends a test broadcast.
  * `python manage.py seed_roles_and_users`: Provisions demo teachers, students, classrooms, and sessions.

---

## 📱 10. Flutter Mobile Client Integration

The Flutter mobile application (`horrorbringer/class_attendance`) connects to these endpoints providing dual interfaces:

### Student Experience
* **Home Dashboard:** Displays enrolled classes, today's schedule, and live check-in cards.
* **QR Scanner:** Integrated camera scanner with instant haptic feedback and device ID transmission.
* **Attendance History:** Chronological log of past attendance records with status filters.
* **Profile & Settings:** View registered guardian contact info, biometric enrollment status, and password change.

### Teacher Experience
* **Today's Teaching Schedule:** List of classes assigned to the primary teacher or co-teacher.
* **Session Controller:** Create sessions with overlap validation, rotate QR codes, or open projector display.
* **Live Roster Screen:** Color-coded roster cards (Present, Late, Absent, Unmarked) with one-tap status overrides.
* **Course Student Manager:** Add or remove enrolled students by Student ID.
* **Reports Screen:** View classroom attendance rate percentages and trigger CSV downloads.
