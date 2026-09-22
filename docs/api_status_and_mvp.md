# Smart Attendance System — API Status & MVP Scope

Reconciles the mobile screen list against what is **actually implemented and tested** in the backend, and confirms all MVP requirements for the demo.

---

## Part 1 — Actual Implementation Status

### Student App

| Screen / Feature | Endpoint | Status | Notes |
|---|---|---|---|
| **Login** | `POST /api/auth/login/` | ✅ **Built** | Returns DRF Token, role (`student`/`teacher`), and profile |
| **Logout** | `POST /api/auth/logout/` | ✅ **Built** | Revokes and deletes token from database |
| **Password Change** | `POST /api/auth/change-password/` | ✅ **Built** | Validates old password and updates securely |
| **Profile View** | `GET /api/students/me/` | ✅ **Built** | Returns ID, full name, classroom, teacher, consent, enrollment count |
| **Home Dashboard (Schedule)** | `GET /api/students/schedule/today/` | ✅ **Built** | Today's sessions with live QR status, check-in status, and times |
| **Check-in — QR** | `POST /api/attendance/checkin/qr/` | ✅ **Built** | Rotating & expiring tokens, 15-min present/late cutoff, subnet guard |
| **Check-in — Face** | `POST /api/attendance/checkin/face/` | ✅ **Built** | Dual-frame liveness & anti-spoofing, RAM-only image processing |
| **Face Enrollment** | `POST /api/face/enroll/` | ✅ **Built** | 1 to 5 multi-angle photo uploads, InsightFace 512-d embeddings |
| **Attendance History** | `GET /api/attendance/history/` | ✅ **Built** | Filterable (`?status=`, `?date_from=`, `?date_to=`), paginated (`limit/offset`) |
| **Notifications / Alerts List** | `GET /api/alerts/mine/` | ✅ **Built** | In-app absence notices history for authenticated student |
| **Profile Edit** | — | 🟡 Deferred | Edit phone/email handled by school admin / Django Admin |

---

### Teacher App

| Screen / Feature | Endpoint | Status | Notes |
|---|---|---|---|
| **Login / Logout / Password** | Same as above | ✅ **Built** | Unified token auth with role branching (`role: "teacher"`) |
| **Today's Classes** | `GET /api/teacher/classes/today/` | ✅ **Built** | Lists all scheduled sessions for the teacher's classrooms |
| **Create Session On-Demand** | `POST /api/teacher/sessions/` | ✅ **Built** | Creates session with auto-generated dynamic QR code |
| **Live Classroom Roster** | `GET /api/teacher/sessions/{id}/roster/` | ✅ **Built** | Full class roll call with Present, Late, Absent, Unmarked counts |
| **Manual Override (Single)** | `PATCH /api/teacher/attendance/{id}/` | ✅ **Built** | 1-click status adjustment with `edited_by` audit stamping |
| **Manual Override (Bulk)** | `POST /api/teacher/sessions/{id}/attendance/bulk/` | ✅ **Built** | Bulk update attendance with a list of `{student_id, status}` |
| **Rotate QR Code** | `POST /api/teacher/sessions/{id}/qr/` | ✅ **Built** | Rotates/refreshes token with custom expiration minutes |
| **Dynamic Auto-QR** | `GET /api/teacher/sessions/{id}/qr/dynamic/` | ✅ **Built** | Auto-refreshing token info for classroom projector |
| **Fullscreen Live QR View** | `GET /api/teacher/sessions/{id}/live-qr/` | ✅ **Built** | Web projector display with live countdown & QR refresh |
| **End Session → Alerts** | `POST /api/teacher/sessions/{id}/end/` | ✅ **Built** | Closes session and triggers async Celery Telegram/Email alerts |
| **Class Reports (Analytics)** | `GET /api/reports/class/{id}/` | ✅ **Built** | Aggregated present, late, absent counts and percentage rates |
| **Student Reports** | `GET /api/reports/student/{id}/` | ✅ **Built** | Individual student attendance breakdown & overall rate |
| **Weekly / Calendar View** | — | 🟡 Deferred | Can filter today's sessions; full calendar scheduled for v2 |

---

## Part 2 — MVP Scope Status

Goal: A robust, demoable end-to-end loop:
**A student can check in (QR or face), a teacher can run a session, see the live roll call, and an absence alert fires on session close.**

### 🟢 MVP-Required Loop — 100% Complete & Tested

- [x] **Login & Logout** (student + teacher) — `POST /api/auth/login/`, `POST /api/auth/logout/`
- [x] **Student Profile** — `GET /api/students/me/`
- [x] **Student Home Dashboard** — `GET /api/students/schedule/today/`
- [x] **QR Code Check-In** — `POST /api/attendance/checkin/qr/`
- [x] **Face Recognition Check-In** — `POST /api/attendance/checkin/face/` (with anti-spoofing)
- [x] **Face Biometric Enrollment** — `POST /api/face/enroll/` (API) & `/students/<id>/enroll/` (Web studio)
- [x] **Teacher Schedule & Session Creation** — `GET /api/teacher/classes/today/`, `POST /api/teacher/sessions/`
- [x] **Teacher Live Roster** — `GET /api/teacher/sessions/{id}/roster/`
- [x] **Manual Overrides (Single & Bulk)** — `PATCH /api/teacher/attendance/{id}/` & `POST /api/teacher/sessions/{id}/attendance/bulk/`
- [x] **Session Finalization & Alerts** — `POST /api/teacher/sessions/{id}/end/` (Celery background worker)
- [x] **Class & Student Reports** — `GET /api/reports/class/{id}/`, `GET /api/reports/student/{id}/`
- [x] **In-App Notifications** — `GET /api/alerts/mine/`

---

## Part 3 — Verification & Developer Tools

1. **Automated Test Coverage:**
   - **35 automated test cases passed** (`python manage.py test`).
   - All critical paths validated: Token lifecycle, QR expiry, Liveness detection, Celery alert dispatch, Single/Bulk manual overrides, Student schedule, and Reports.

2. **Interactive Testing Consoles:**
   - **Interactive Swagger UI:** [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)
   - **OpenAPI 3.0 Spec:** [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/)
   - **System Healthcheck:** [http://localhost:8000/api/health/](http://localhost:8000/api/health/)

3. **Handbooks for Team Members:**
   - [docs/mobile_api_guide.md](mobile_api_guide.md) — Detailed Flutter integration guide with code snippets and test accounts.
   - [docs/api_reference.md](api_reference.md) — Full endpoint reference with JSON payloads and response schemas.
