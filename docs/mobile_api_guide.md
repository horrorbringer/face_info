# Mobile Developer Guide & API Integration Report (Flutter)

**Target Audience:** Mobile Developers (Nov Thearith), QA Testing (Sem Sreyneat)  
**Target Platform:** Flutter (Android & iOS)  
**API Version:** `v1`  
**Base URL:**
- Local Android Emulator: `http://10.0.2.2:8000/api`
- Local iOS Simulator: `http://127.0.0.1:8000/api`
- Local Physical Device (LAN): `http://<YOUR_LOCAL_IP>:8000/api`
- Interactive Swagger Console: `http://<SERVER_HOST>:8000/api/docs/`
- OpenAPI JSON Schema: `http://<SERVER_HOST>:8000/api/schema/`

---

## 1. Authentication Architecture & Token Lifecycle

### A. Token Management
Every authenticated request requires the HTTP header:
```http
Authorization: Token <user_token>
Content-Type: application/json
```

- Store the token securely using `flutter_secure_storage` or EncryptedSharedPreferences / Keychain.
- **Login:** Send `POST /api/auth/login/` with `username` and `password`. The response provides:
  - `token`: Unique DRF token key.
  - `role`: `"student"` or `"teacher"`.
  - `student` / `teacher`: Associated profile details.
- **Role-Based Navigation:**
  - If `role == "student"` -> Navigate to **Student Dashboard** (`/student/home`).
  - If `role == "teacher"` -> Navigate to **Teacher Dashboard** (`/teacher/classes`).
- **Logout:** Call `POST /api/auth/logout/` before clearing local storage. This revokes the token on the server so discarded tokens cannot be replayed.
- **Password Change:** Call `POST /api/auth/change-password/` with `old_password`, `new_password`, and `confirm_password`.

### B. Pre-Seeded Test Accounts for Mobile Testing
| Role | Username | Password | Auth Token |
| :--- | :--- | :--- | :--- |
| **Teacher (CS-101)** | `teacher_sokha` | `TeacherPassword123!` | `d46d3ee89cbfb6c5e7f63a9d9351bfdbd6042f66` |
| **Teacher (SE-201)** | `teacher_vanny` | `TeacherPassword123!` | `ff45798f44617dae9acc9c4822207a5c6aa755e9` |
| **Student (Dara)** | `student_dara` | `StudentPassword123!` | `504a0d28f24a975e7717a18bbf1c8010527c4877` |
| **Student (Bopha)**| `student_bopha` | `StudentPassword123!` | `32b8e6046cf09b76a8544e1462fd48d66bdca1d5` |
| **Admin** | `admin` | `AdminPassword123!` | `f6ae2ed7a9d47b8b495c41a3024803d685684d7a` |

---

## 2. Student Mobile App Workflows

### Flow 1: QR Code Attendance Check-In
Using Flutter package: `mobile_scanner` or `qr_code_scanner`.

```
[Mobile Camera] -> Scans QR -> Obtains `qr_token` String
       |
       v
POST /api/attendance/checkin/qr/  { "qr_token": "<scanned_token>" }
       |
       +---> 201 Created: { "message": "Successfully checked in (present)." }
       +---> 200 OK:      { "message": "Already checked in for this session." }
       +---> 400 Bad Req: { "error": "QR code has expired..." }
       +---> 403 Forbidden: Campus subnet or classroom enrollment restriction
```

**Flutter Implementation Note:**
- If user scans within 15 minutes of session start: recorded as `Present`.
- If user scans after cutoff: automatically marked as `Late`.
- QR tokens can be either static tokens or rotating tokens (prefixed with `dyn_`). The backend transparently validates both.

---

### Flow 2: Live Biometric Face Check-In
Using Flutter package: `camera`.

```
[Camera Controller] -> Capture high-res frame -> `XFile`
       |
       v
POST /api/attendance/checkin/face/
Body: MultipartRequest
  - field: "session_id" (optional int)
  - file:  "image" (JPEG/PNG bytes)
       |
       +---> 200 OK:
             {
               "matched": true,
               "student_name": "Dara Pich",
               "confidence_score": 0.892,
               "liveness": { "status": "verified" }
             }
       +---> 400 Bad Request:
             { "error": "No confident match. Please use manual lookup..." }
       +---> 403 Forbidden:
             { "error": "Anti-spoofing alert: Screen or paper replay detected." }
```

---

### Flow 3: Student Attendance History & Filtered Pagination
- **Endpoint:** `GET /api/attendance/history/`
- **Supported Query Parameters:**
  - `?status=present` / `?status=late` / `?status=absent`
  - `?date_from=2026-09-01`
  - `?date_to=2026-09-30`
  - `?limit=20&offset=0`
- **Flutter UI Recommendation:** Use a `TabBar` (`All`, `Present`, `Late`, `Absent`) and pass `?status=` when toggling tabs.

---

## 3. Teacher Mobile App Workflows

### Flow 1: Today's Scheduled Classes
- **Endpoint:** `GET /api/teacher/classes/today/`
- Returns an array of scheduled sessions for the teacher's classrooms.
- Displays class name, start/end time, `is_ended` status, and current QR code validity.

### Flow 2: Create Session On-Demand
- **Endpoint:** `POST /api/teacher/sessions/`
- **Payload:**
```json
{
  "class_room": 1,
  "date": "2026-09-22",
  "start_time": "08:00:00",
  "end_time": "10:00:00",
  "auto_generate_qr": true,
  "qr_expiry_minutes": 120
}
```
- Instantly creates session and returns a fresh QR code token.

### Flow 3: Live Class Roster & Roll Call
- **Endpoint:** `GET /api/teacher/sessions/<session_id>/roster/`
- **Sample Response:**
```json
{
  "session_id": 1,
  "class_room": "Computer Science 101",
  "date": "2026-09-22",
  "is_ended": false,
  "summary": {
    "present": 18,
    "late": 2,
    "absent": 0,
    "unmarked": 5,
    "total": 25
  },
  "roster": [
    {
      "student_pk": 2,
      "student_id": "STU001",
      "student_name": "Dara Pich",
      "attendance_status": "present",
      "method": "qr",
      "checked_in_at": "2026-09-22T08:05:10Z",
      "record_id": 42
    },
    {
      "student_pk": 3,
      "student_id": "STU002",
      "student_name": "Bopha Keo",
      "attendance_status": "unmarked",
      "method": null,
      "record_id": null
    }
  ]
}
```

### Flow 4: Teacher Manual Override (1-Click Fix)
- **Endpoint:** `PATCH /api/teacher/attendance/<record_id>/`
- **Payload:** `{ "status": "present" }` or `{ "status": "late" }` or `{ "status": "absent" }`
- Used when a student forgot their phone or teacher grants manual attendance.

### Flow 5: Finalize Session & Trigger Absence Alerts
- **Endpoint:** `POST /api/teacher/sessions/<session_id>/end/`
- Stamps `ended_at = now()` and automatically dispatches async Celery background tasks to notify parents/guardians via Telegram or Email for all students with no attendance record.

---

## 4. HTTP Status Code & Error Handling Matrix

| HTTP Code | Scenario | Recommended Mobile App Behavior |
| :---: | :--- | :--- |
| **`200 OK`** | Successful query or idempotent check-in | Render data or display existing check-in badge |
| **`201 Created`** | Successful check-in / session creation | Show success animation / haptic feedback |
| **`400 Bad Request`** | Expired QR token, invalid credentials, or low confidence match | Show friendly banner with teacher fallback option |
| **`401 Unauthorized`** | Token missing, invalid, or revoked | Redirect immediately to `LoginScreen` and clear secure storage |
| **`403 Forbidden`** | Campus IP subnet restriction, student not enrolled in class, or spoof alert | Show clear explanatory alert dialog |
| **`404 Not Found`** | Profile or session does not exist | Show empty state |
| **`503 Unavailable`** | Backend service or database degraded | Show "Server maintenance" retry screen |

---

## 5. Developer Tools & Code Generation

1. **Interactive Swagger Console:**
   Open `http://localhost:8000/api/docs/` in any browser to execute live requests and test tokens without writing code.
2. **Flutter Model & Client Generation:**
   Generate strongly typed Dart models directly from the OpenAPI 3.0 schema:
   ```bash
   # Download the OpenAPI spec
   curl -o openapi.json http://localhost:8000/api/schema/

   # Generate Dart models via openapi-generator
   npx @openapitools/openapi-generator-cli generate \
     -i openapi.json \
     -g dart-dio \
     -o ./lib/api_client
   ```
