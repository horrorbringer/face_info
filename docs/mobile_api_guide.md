# Mobile Developer Guide & API Integration Report (Flutter)

**Target Audience:** Mobile Developers (Nov Thearith), QA Testing (Sem Sreyneat)  
**Target Platform:** Flutter (Android & iOS)  
**API Version:** `v1`  
**Interactive API Docs (Swagger):** `/api/docs/`  
**OpenAPI 3.0 Schema:** `/api/schema/`  

---

## 1. Network Architecture, Base URLs & Health Check

### A. Environment-Based Base URL (`--dart-define`)
Avoid hardcoding IP addresses in Flutter code. Configure dynamic resolution with compile-time fallbacks:

```dart
class ApiConstants {
  static const String baseUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: _defaultLocalUrl,
  );

  static String get _defaultLocalUrl {
    // 1. Android Emulator loopback
    if (Platform.isAndroid) return 'http://10.0.2.2:8000/api';
    // 2. iOS Simulator loopback
    if (Platform.isIOS) return 'http://127.0.0.1:8000/api';
    // 3. Fallback LAN / Staging
    return 'https://student-attendance.vanny.monster/api';
  }

  // Health
  static const String health = '/health/';

  // Auth & Student Profile
  static const String login = '/auth/login/';
  static const String logout = '/auth/logout/';
  static const String changePassword = '/auth/change-password/';
  static const String studentProfile = '/students/me/';
  static const String scheduleToday = '/students/schedule/today/';
  static const String studentAlerts = '/alerts/mine/';
  static String studentReport(int id) => '/reports/student/$id/';

  // Attendance & Face
  static const String checkinQr = '/attendance/checkin/qr/';
  static const String checkinFace = '/attendance/checkin/face/';
  static const String attendanceHistory = '/attendance/history/';
  static const String faceEnroll = '/face/enroll/';

  // Teacher Endpoints
  static const String teacherClasses = '/teacher/classes/today/';
  static const String teacherSessions = '/teacher/sessions/';
  static String teacherRoster(int sessionId) => '/teacher/sessions/$sessionId/roster/';
  static String teacherLiveFeed(int sessionId) => '/teacher/sessions/$sessionId/live-feed/';
  static String teacherBulkAttendance(int sessionId) => '/teacher/sessions/$sessionId/attendance/bulk/';
  static String teacherDynamicQr(int sessionId) => '/teacher/sessions/$sessionId/qr/dynamic/';
  static String teacherEndSession(int sessionId) => '/teacher/sessions/$sessionId/end/';
  static String teacherOverride(int recordId) => '/teacher/attendance/$recordId/';
  static String exportCsv(int classId) => '/reports/class/$classId/export-csv/';
}
```

Run Flutter apps with your target backend:
```bash
# Point directly to remote staging/production backend:
flutter run --dart-define=API_URL=https://student-attendance.vanny.monster/api

# Or target local developer machine across Wi-Fi:
flutter run --dart-define=API_URL=http://192.168.1.100:8000/api
```

### B. Connectivity Ping (`GET /api/health/`)
* **Endpoint:** `GET /api/health/` (Public, no auth required)
* **Response (200 OK):**
```json
{
  "status": "healthy",
  "database": "connected",
  "face_engine": "available",
  "active_sessions": 2,
  "timestamp": "2026-09-23T14:10:00.000Z"
}
```
* **Mobile Usage:** Ping during splash screen or when recovering from airplane mode to verify backend connectivity before requesting user login.

---

## 2. Authentication Architecture & Token Lifecycle

### A. Token Management
Every authenticated request requires the HTTP header:
```http
Authorization: Token <user_token>
Content-Type: application/json
```
*(Backend also flexibly accepts `Bearer <token>` or plain `<token>`)*.

### B. Login (`POST /api/auth/login/`)
* **Endpoint:** `POST /api/auth/login/`
* **Request Body:**
```json
{
  "username": "student_dara",
  "password": "StudentPassword123!"
}
```
* **Success Response (200 OK):**
```json
{
  "token": "504a0d28f24a975e7717a18bbf1c8010527c4877",
  "user_id": 14,
  "username": "student_dara",
  "role": "student",
  "student": {
    "id": 2,
    "student_id": "STU001",
    "name": "Dara Pich"
  },
  "teacher": null
}
```
* **Role-Based Navigation:**
  - `role == "student"` $\rightarrow$ Store token, route to **Student Shell Scaffold** (`/student/home`).
  - `role == "teacher"` $\rightarrow$ Store token, route to **Teacher Shell Scaffold** (`/teacher/classes`).

### C. Logout & Revocation (`POST /api/auth/logout/`)
* **Endpoint:** `POST /api/auth/logout/`
* **Response (200 OK):** `{"message": "Successfully logged out. Token revoked."}`
* Call this before clearing `FlutterSecureStorage` so discarded tokens cannot be replayed.

### D. Change Password (`POST /api/auth/change-password/`)
* **Endpoint:** `POST /api/auth/change-password/`
* **Payload:** `{"old_password": "...", "new_password": "...", "confirm_password": "..."}`
* **Response (200 OK):** `{"message": "Password changed successfully.", "token": "<new_token>"}`

### E. Pre-Seeded Test Accounts
| Role | Username | Password | Auth Token |
| :--- | :--- | :--- | :--- |
| **Teacher (CS-101)** | `teacher_sokha` | `TeacherPassword123!` | `d46d3ee89cbfb6c5e7f63a9d9351bfdbd6042f66` |
| **Teacher (SE-201)** | `teacher_vanny` | `TeacherPassword123!` | `ff45798f44617dae9acc9c4822207a5c6aa755e9` |
| **Student (Dara)** | `student_dara` | `StudentPassword123!` | `504a0d28f24a975e7717a18bbf1c8010527c4877` |
| **Student (Bopha)**| `student_bopha` | `StudentPassword123!` | `32b8e6046cf09b76a8544e1462fd48d66bdca1d5` |
| **Admin** | `admin` | `AdminPassword123!` | `f6ae2ed7a9d47b8b495c41a3024803d685684d7a` |

---

## 3. Student Mobile App Workflows

### Flow 0: First-Time Login & Face Enrollment Onboarding
Upon student login, fetch their profile: `GET /api/students/me/`.

```json
{
  "id": 2,
  "student_id": "STU001",
  "full_name": "Dara Pich",
  "class_room": { "id": 1, "name": "CS-101" },
  "face_embeddings_count": 0,
  "consent_given_at": null
}
```

* If `face_embeddings_count == 0` $\rightarrow$ Present **Soft-Prompt Bottom Sheet** (non-blocking).
* If user chooses *"Enroll My Face Now"*, launch camera to capture 1 to 3 angles (Front, Slight Left, Slight Right).

#### Multi-Angle Face Upload (`POST /api/face/enroll/`)
* **Content-Type:** `multipart/form-data`
* **Form Field:** `images` (List of binary JPEG/PNG files) or `image` (single file).
* **Dio Implementation Snippet:**
```dart
Future<bool> enrollFaceAngles(List<String> imagePaths) async {
  final formData = FormData();

  for (final path in imagePaths) {
    formData.files.add(MapEntry(
      'images',
      await MultipartFile.fromFile(path, filename: 'angle_${DateTime.now().millisecondsSinceEpoch}.jpg'),
    ));
  }

  try {
    final response = await dio.post('/face/enroll/', data: formData);
    // Success: 201 Created
    // {
    //   "message": "Successfully enrolled 3 face template(s).",
    //   "enrolled_count": 3,
    //   "total_templates": 3,
    //   "errors": null
    // }
    return response.statusCode == 201;
  } on DioException catch (e) {
    // 400 Bad Request with partial errors
    final errors = e.response?.data['errors'];
    print('Enrollment error: $errors');
    return false;
  }
}
```

---

### Flow 1: QR Code Attendance Check-In
* **Endpoint:** `POST /api/attendance/checkin/qr/`
* **Payload:** `{ "qr_token": "<scanned_token>" }`
* Supports both static session tokens and dynamic rotating tokens (prefixed `dyn_`).

```
[Mobile Camera] -> Scans QR -> Obtains `qr_token`
       |
       v
POST /api/attendance/checkin/qr/
       |
       +---> 201 Created:    { "message": "Successfully checked in (present)." }
       +---> 200 OK:         { "message": "Already checked in for this session." }
       +---> 400 Bad Req:    { "error": "QR code has expired..." }
       +---> 403 Forbidden:  { "error": "Attendance check-in is restricted to the authorized campus network." }
       +---> 429 Throttle:   { "detail": "Request was throttled. Expected available in 10 seconds." }
```

#### Special Status Handling:
1. **Campus Wi-Fi Subnet Restriction (403):**  
   If the student scans using cellular data outside school, show a popup:  
   👉 *"Please connect to the School Campus Wi-Fi to verify attendance."*
2. **Rate-Limiting (429):**  
   Check-in endpoints have `throttle_scope = "attendance_checkin"`. Prevent rapid double-tapping by disabling the scan trigger with a boolean flag (`_isProcessing = true`).

---

### Flow 2: Live Biometric Face Check-In
* **Endpoint:** `POST /api/attendance/checkin/face/`
* **Content-Type:** `multipart/form-data`
* **Form Fields:** `image` (binary file) and optional `session_id` (integer).
* **Response (200 OK):**
```json
{
  "matched": true,
  "student_name": "Dara Pich",
  "confidence_score": 0.892,
  "liveness": { "status": "verified" }
}
```

---

### Flow 3: Student Attendance History & Filtered Pagination
* **Endpoint:** `GET /api/attendance/history/`
* **Query Parameters:**
  - `?status=present` / `?status=late` / `?status=absent`
  - `?date_from=2026-09-01&date_to=2026-09-30`
  - `?limit=20&offset=0`
* **Response (200 OK):**
```json
{
  "count": 45,
  "next": ".../api/attendance/history/?limit=20&offset=20",
  "previous": null,
  "results": [
    {
      "id": 101,
      "student": 2,
      "student_id": "STU001",
      "student_name": "Dara Pich",
      "session": 15,
      "class_room_name": "Computer Science 101",
      "status": "present",
      "method": "qr",
      "checked_in_at": "2026-09-22T08:05:10Z",
      "confidence_score": null,
      "is_deleted": false
    }
  ]
}
```

---

### Flow 4: Student In-App Alerts & Notification Badge
* **Endpoint:** `GET /api/alerts/mine/`
* **Usage:** Powers the notification bell icon on the Student Dashboard.
* **Response (200 OK):**
```json
[
  {
    "id": 4,
    "session": 12,
    "session_name": "Computer Science 101",
    "session_date": "2026-09-21",
    "channel": "telegram",
    "status": "sent",
    "sent_at": "2026-09-21T10:05:00Z",
    "error_message": ""
  }
]
```

---

### Flow 5: Student Attendance Analytics & Dashboard Summary
* **Endpoint:** `GET /api/reports/student/<student_id>/`
* **Usage:** Populates circular progress indicators, attendance rate percentage, and breakdown cards on the Home Dashboard.
* **Response (200 OK):**
```json
{
  "student_id": "STU001",
  "student_name": "Dara Pich",
  "class_room": "Computer Science 101",
  "total_recorded_sessions": 24,
  "present_count": 21,
  "late_count": 2,
  "absent_count": 1,
  "attendance_rate": 87.5
}
```

---

## 4. Teacher Mobile App Workflows

### Flow 1: Today's Scheduled Classes
* **Endpoint:** `GET /api/teacher/classes/today/`
* **Response (200 OK):**
```json
[
  {
    "id": 1,
    "class_room": { "id": 1, "name": "Computer Science 101" },
    "date": "2026-09-23",
    "start_time": "08:00:00",
    "end_time": "10:00:00",
    "qr_token": "dyn_1_abc123",
    "is_ended": false,
    "is_qr_valid": true
  }
]
```

---

### Flow 2: Create Session On-Demand
* **Endpoint:** `POST /api/teacher/sessions/`
* **Payload:**
```json
{
  "class_room": 1,
  "date": "2026-09-23",
  "start_time": "08:00:00",
  "end_time": "10:00:00",
  "auto_generate_qr": true,
  "qr_expiry_minutes": 120
}
```

---

### Flow 3: Live Class Roster & Roll Call
* **Endpoint:** `GET /api/teacher/sessions/<session_id>/roster/`
* Returns enrolled roster, attendance status, and check-in method (`qr`, `face`, or `manual`).

---

### Flow 4: Teacher 1-Click Manual Override
* **Endpoint:** `PATCH /api/teacher/attendance/<record_id>/`
* **Payload:** `{ "status": "present" }` / `{ "status": "late" }` / `{ "status": "absent" }`

---

### Flow 5: Teacher Bulk Attendance Override
* **Endpoint:** `POST /api/teacher/sessions/<session_id>/attendance/bulk/`
* **Usage:** Allows the teacher to mark all remaining unmarked students as absent with a single tap, or perform batch attendance updates.
* **Payload:**
```json
{
  "records": [
    { "student_id": "STU001", "status": "present" },
    { "student_id": "STU002", "status": "absent" },
    { "student_id": "STU003", "status": "late" }
  ]
}
```
* **Response (200 OK):**
```json
{
  "message": "Bulk attendance updated.",
  "updated_count": 3,
  "errors": []
}
```

---

### Flow 6: Live Attendance Polling Ticker & Lifecycle Management
* **Endpoint:** `GET /api/teacher/sessions/<session_id>/live-feed/?since=<server_time>`
* **Response (200 OK):**
```json
{
  "session_id": 1,
  "class_room": "Computer Science 101",
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

#### Flutter App Lifecycle Management (Preventing Battery Drain):
Always pause the 3-second polling timer when the screen is navigated away or the application is sent to the background:

```dart
class _TeacherLiveFeedScreenState extends State<TeacherLiveFeedScreen> with WidgetsBindingObserver {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _startPolling();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _stopPolling();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused || state == AppLifecycleState.inactive) {
      _stopPolling(); // Stop polling when app in background
    } else if (state == AppLifecycleState.resumed) {
      _startPolling(); // Resume when teacher returns
    }
  }

  void _startPolling() {
    _timer?.cancel();
    _fetchLiveFeed();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _fetchLiveFeed());
  }

  void _stopPolling() {
    _timer?.cancel();
    _timer = null;
  }
}
```

---

### Flow 7: Finalize Session & Trigger Absence Alerts
* **Endpoint:** `POST /api/teacher/sessions/<session_id>/end/`
* **Payload:** `{}`
* Marks all remaining unmarked students as absent and triggers asynchronous Celery tasks to send automated Telegram/Email notices to parents.

---

### Flow 8: Export Classroom Attendance CSV
* **Endpoint:** `GET /api/reports/class/<class_id>/export-csv/`
* **Query Params (Optional):** `?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
* Returns raw `text/csv` stream for direct saving or sharing via device share sheet.

---

## 5. HTTP Status Code & Error Handling Matrix

| HTTP Code | Scenario | Recommended Mobile App Behavior & UX Message |
| :---: | :--- | :--- |
| **`200 OK`** | Successful query or duplicate check-in | Render data or display *"Already checked in"* badge. |
| **`201 Created`** | Successful check-in / session creation | Trigger light haptic buzz & show green checkmark dialog. |
| **`400 Bad Request`** | Expired QR token, invalid format, or blurry selfie | Show friendly banner highlighting the issue (e.g., *"Photo 2 is too dark. Please retry."*). |
| **`401 Unauthorized`** | Token expired or user signed in elsewhere | Clear `FlutterSecureStorage` and redirect immediately to `LoginScreen`. |
| **`403 Forbidden`** | Campus Wi-Fi restriction or spoofing detected | Alert: *"Check-in is restricted to the authorized campus Wi-Fi network."* |
| **`404 Not Found`** | Profile or session not found | Render clean empty state illustration with retry button. |
| **`429 Too Many Requests`** | Check-in throttle exceeded (rapid scanning) | Show *"Please wait a few seconds before trying again."* |
| **`503 Unavailable`** | Server offline or database maintenance | Show *"Server undergoing maintenance"* retry screen. |

---

## 6. Developer Tools & Code Generation

### 1. Interactive Swagger Console
Open `http://localhost:8000/api/docs/` in any browser to execute live requests and test tokens without writing code.

### 2. Automated Model Generation via OpenAPI
Generate strongly-typed Dart models and client code directly from the Django backend:

```bash
# 1. Download OpenAPI specification from live backend
curl -o openapi.json http://localhost:8000/api/schema/

# 2. Generate Dart Dio API client
npx @openapitools/openapi-generator-cli generate \
  -i openapi.json \
  -g dart-dio \
  -o ./lib/core/api_client
```
