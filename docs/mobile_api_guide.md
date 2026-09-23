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

### Flow 0: First-Time Login & Face Enrollment Onboarding (UX Architecture & Policy)

When a student logs into the mobile app for the first time, check their profile via `GET /api/students/me/` to inspect `face_embeddings_count`:

```dart
// Recommended Mobile App Onboarding Router
final profile = await api.getStudentProfile();
if (profile.faceEmbeddingsCount == 0 && !hasSeenEnrollmentPrompt) {
  showEnrollmentPromptBottomSheet();
} else {
  navigateToHomeDashboard();
}
```

#### A. The Recommended "Soft-Prompt" Bottom Sheet
**Never hard-block a student from entering the app or checking into class.** If a student is running late on day one, a mandatory photo enrollment will cause them to miss attendance. Instead, show a friendly non-blocking sheet:

```
+-------------------------------------------------------------+
|  📸 Enable Hands-Free Face Check-In                        |
|                                                             |
|  Walk past campus entrance kiosks without taking out your   |
|  phone. It only takes 30 seconds to set up.                 |
|                                                             |
|  [  Enroll My Face Now (Camera)  ]  --> Primary Action      |
|  [  Remind Me Later              ]  --> Neutral Dismiss     |
|  [  I prefer to use QR Code only ]  --> Privacy Opt-Out     |
+-------------------------------------------------------------+
```

1. **"Enroll My Face Now"**:
   - Opens in-app live selfie camera viewfinder.
   - Captures 1 to 3 quick angles (Front, Slight Left, Slight Right).
   - Sends multipart request to `POST /api/face/enroll/` with images.
   - Automatically stamps `consent_given_at` and unlocks entrance kiosk matching.
2. **"Remind Me Later"**:
   - Takes them straight to the Home Dashboard so they can attend class.
   - Shows a subtle dashboard reminder banner: *"Face check-in not set up (Tap to complete)"*.
3. **"I prefer to use QR Code only" (Privacy Opt-Out)**:
   - Sets a local flag so they are never prompted again.
   - Complies with student privacy regulations (GDPR/FERPA). Unenrolled students check in 100% via QR code.

---

#### B. First-Time Mobile Edge Cases & Handling Scenarios

| Scenario | Risk / Problem | Recommended Mobile App Behavior |
| :--- | :--- | :--- |
| **1. Friend Fraud (Spoofing)** | Student tries to upload a friend's photo so the friend can fake attendance. | **Disable camera gallery upload** on mobile enrollment. Force live front camera capture with active blink or head turn verification. *(Or require in-person staff enrollment at `/students/<id>/enroll/`)*. |
| **2. Unenrolled at Kiosk** | Student walks up to the campus entrance kiosk before enrolling. | Kiosk displays: `⚠️ No Match Found`. Subtitle instructs: *"Not enrolled yet? Scan QR in your mobile app or see your teacher."* Automatically resets in 2s. |
| **3. Poor Lighting / Blur** | Student takes photo in a dark room or with glasses glare. | Backend (`POST /api/face/enroll/`) validates face clarity. If invalid, returns `400 Bad Request` (`"No clear face detected in photo 2. Please move to a brighter area."`). Mobile highlights the failed angle for quick retry. |
| **4. Appearance Change** | Student changes hairstyle, grows a beard, or gets new glasses. | In **Profile > Face Biometrics**, provide **"Add Extra Angle"** (appends a new vector to `StudentFace` to improve accuracy) and **"Delete & Re-enroll"**. |
| **5. Minor Consent (< 18)** | Biometric regulations requiring parental/guardian consent. | Include a mandatory consent checkbox on the selfie screen: `[x] I (or my guardian) consent to the storage of mathematical face templates for school attendance.` |

---

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
- **Payload:** `{}`
- Stamps `ended_at = now()` and automatically dispatches async Celery background tasks to notify parents/guardians via Telegram or Email for all students with no attendance record.

### Flow 6: Export Classroom Attendance CSV
- **Endpoint:** `GET /api/reports/class/<class_id>/export-csv/`
- **Query Params (Optional):** `?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- **Response:** `text/csv` stream with header `Content-Disposition: attachment; filename="attendance_report_<class>_<date>.csv"`.
- **Columns:** `Session Date, Classroom, Session Time, Student ID, Full Name, Status, Check-In Method, Checked In At, Confidence Score, Edited By`.

### Flow 7: Live Attendance Polling Feed (Real-Time Counter)
- **Endpoint:** `GET /api/teacher/sessions/<session_id>/live-feed/`
- **Query Params (Optional):** `?since=2026-09-23T08:00:00Z`
- **Response:**
  ```json
  {
    "session_id": 1,
    "class_room": "Computer Science 101",
    "date": "2026-09-23",
    "is_ended": false,
    "total_enrolled": 30,
    "checked_in_count": 22,
    "present_count": 20,
    "late_count": 2,
    "absent_count": 0,
    "unmarked_count": 8,
    "server_time": "2026-09-23T08:15:30.123456Z",
    "recent_checkins": [
      {
        "record_id": 45,
        "student_id": "STU001",
        "student_name": "Dara Pich",
        "status": "present",
        "method": "face",
        "confidence_score": 0.9412,
        "checked_in_at": "2026-09-23T08:14:55Z"
      }
    ]
  }
  ```
- **Mobile Integration:** Poll every 3–5 seconds while the teacher's active session screen is open to animate new check-ins and live counters.



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
