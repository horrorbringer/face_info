# System Capabilities — Smart Attendance System

This document outlines all features, operational capabilities, and services currently supported by the Smart Attendance System.

---

## 1. Attendance Check-In (3 Supported Methods)

### A. Dynamic QR Code Check-In
- **Endpoint:** `POST /api/attendance/checkin/qr/`
- **Payload:** `{ "qr_token": "<token>" }`
- **Capabilities:**
  - **Dynamic Expiration:** QR codes are session-bound and expire automatically (default: 10 minutes).
  - **On-Demand Rotation:** Teachers can rotate/refresh the QR code anytime from their app or dashboard.
  - **Smart Cutoff Calculation:**
    - If checked in within `LATE_THRESHOLD_MINUTES` (default: 15 mins) of class start: recorded as **`Present`**.
    - If checked in after the threshold: recorded as **`Late`**.
  - **Idempotency:** Re-scanning the QR code returns the existing confirmed check-in without creating duplicate records.
  - **Class Validation:** Students can only check in to classes they are actively enrolled in.

### B. Face Recognition Check-In
- **Endpoint:** `POST /api/attendance/checkin/face/`
- **Payload:** Multipart form data (`image: <frame>`, optional `session_id: <id>`)
- **Capabilities:**
  - **Optimized Search Space:** Compares live capture embeddings only against active students enrolled in that session's classroom, keeping lookup time fast.
  - **Cosine Similarity Threshold:** Matches against stored embeddings using `MATCH_THRESHOLD` (default: `0.5`).
  - **Low-Confidence Fallback:** Does not guess if confidence is below threshold; returns a friendly response directing the student to use QR check-in or request teacher assistance.
  - **Privacy by Design:** Camera frames are parsed directly in RAM using OpenCV and **never written to disk or the database**.
  - **Note on v1 Scope:** Liveness detection (anti-spoofing) is out of scope for v1.

### C. Manual Teacher Overrides & Audits
- **Endpoint:** `PATCH /api/teacher/attendance/{id}/`
- **Payload:** `{ "status": "present" | "late" | "absent", "is_deleted": true | false }`
- **Capabilities:**
  - Teachers can correct any attendance record.
  - **Audit Stamp:** Automatically assigns `edited_by = request.user` for traceability.
  - **Soft Deletion:** Records are soft-deleted (`is_deleted=True`) rather than permanently purged.

---

## 2. Automated Absence Notifications (Celery + Background Workers)

- **Trigger Endpoint:** `POST /api/teacher/sessions/{id}/end/`
- **Capabilities:**
  - **One-Click Closure:** The teacher ends the class session, timestamping `ended_at = now()`.
  - **Non-Blocking Execution:** Hands off absence checking and notifications to an asynchronous **Celery worker** via Redis broker.
  - **Automated Absence Marking:** Active students enrolled in the class without an existing attendance record are marked **`Absent`**.
  - **Multi-Channel Dispatch:**
    1. **Telegram Bot API:** Dispatches formatted absence notices to the guardian's chat ID or Telegram handle.
    2. **SMTP Email Fallback:** If Telegram contact is unavailable, dispatches an email notice to the guardian's email address.
  - **Idempotent Audit Log (`AlertLog`):**
    - Tracks delivery status (`sent` or `failed`) with exact error messages.
    - Prevents double-sending notifications if a session is processed multiple times.

---

## 3. Biometric Face Enrollment

Biometric enrollment can be performed via either the REST API (for mobile/external integrations) or the interactive Staff Web Portal.

### A. REST API Endpoint
- **Endpoint:** `POST /api/face/enroll/`
- **Payload:** Multipart form data (`images: [<file1>, <file2>, ...]`, optional `student_id`)
- **Capabilities:**
  - **Multi-Angle Support:** Supports uploading 1 to 5 face photos at different angles for improved recognition accuracy.
  - **Embedding Extraction:** Extracts normalized 512-dimensional facial embeddings using InsightFace and saves them to `StudentFace`.
  - **Consent Logging:** Automatically stamps `consent_given_at = now()`.

### B. Staff Web Portal (`/students/<student_id>/enroll/`)
- **Web Interface:** Interactive Bootstrap 5 enrollment studio for staff members with `students.change_student` permission.
- **Capabilities:**
  - **Live Webcam Studio:** In-browser camera viewfinder with an oval face-positioning guide, angle selector (`Front`, `Slight Left`, `Slight Right`, `Slight Up`), and frame snapshot gallery capturing 1 to 5 biometric angles.
  - **File Upload Fallback:** File chooser with instant client-side thumbnail previews.
  - **Dual-Model Synchronization:** Simultaneously saves templates into both `attendance.models.StudentFace` (enabling attendance face check-in) and `students.models.FaceEmbedding` (enabling kiosk cosine matching).
  - **Consent & Compliance Certification:** Requires entering a consent document reference and checking a mandatory certification box before biometric template generation.
  - **Safe Revocation:** Displays enrollment status badge (`✓ Enrolled (N templates)` vs `Not Enrolled`). Includes a guarded Bootstrap confirmation modal and standalone confirmation page (`/students/<student_id>/revoke/`) to permanently purge all facial templates from the database.

---

## 4. Teacher & Classroom Operations

- **Today's Classes:** `GET /api/teacher/classes/today/`
  - Fetches all sessions scheduled for today for the authenticated teacher.
- **Dynamic QR Code Generation:** `POST /api/teacher/sessions/{id}/qr/`
  - Generates a cryptographically secure 32-byte token with configurable expiration minutes.
- **Session Finalization:** `POST /api/teacher/sessions/{id}/end/`
  - Marks class closed and triggers background alerts.

---

## 5. Student Self-Service

- **Student Profile:** `GET /api/students/me/`
  - Fetches student ID, name, classroom name, guardian contact, and face enrollment count.
- **Attendance History:** `GET /api/attendance/history/`
  - Retrieves personal attendance records across all past sessions.

---

## 6. Analytics & Attendance Reports

- **Classroom Report:** `GET /api/reports/class/{id}/`
  - Total enrolled students.
  - Total sessions held.
  - Present count & percentage.
  - Late count & percentage.
  - Absence count & percentage.
- **Student Report:** `GET /api/reports/student/{id}/`
  - Total recorded sessions for the student.
  - Individual attendance rate (%) and status breakdown.

---

## 7. Web Portals & Administrative Tools

- **Staff Admin Portal (`/admin/`):**
  - Full CRUD for Teachers, Classrooms, Sessions, Students, AttendanceRecords, and AlertLogs.
  - Built-in admin actions for bulk soft-delete and restore.
  - CSV student import tool.
- **Web Kiosk Scanner (`/`):**
  - Web camera interface for staff-assisted student lookups.

---

## 8. Supported Client Integrations

The system exposes clean REST endpoints consumable by:
- **Flutter Mobile Apps** (Student & Teacher mobile clients via Token Authentication).
- **Web Applications** (Via Session or Token Authentication).
- **Kiosk Hardware** (Camera terminal for entrance/gate check-in).
