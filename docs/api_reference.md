# Smart Attendance System — API Reference (Flutter Integration)

**Base URL:** `http://localhost:8000/api` (Local) or your server address.

All authenticated endpoints require the header:
```http
Authorization: Token <your_token_here>
```

---

## 1. Authentication & Profile

### `POST /api/auth/login/`
Authenticates a student or teacher user and returns an API token and role.

- **Auth Required:** No
- **Content-Type:** `application/json`
- **Request Body:**
```json
{
  "username": "student1",
  "password": "password123"
}
```
- **Response `200 OK`:**
```json
{
  "token": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b",
  "user_id": 2,
  "username": "student1",
  "role": "student",
  "student": {
    "id": 1,
    "student_id": "STU001",
    "name": "Alice Johnson"
  },
  "teacher": null
}
```

---

### `GET /api/students/me/`
Returns current profile for the authenticated student.

- **Auth Required:** Yes (`Token <token>`)
- **Response `200 OK`:**
```json
{
  "id": 1,
  "student_id": "STU001",
  "full_name": "Alice Johnson",
  "name": "Alice Johnson",
  "class_year": "Year 2",
  "is_active": true,
  "guardian_contact": "@parent_telegram",
  "class_room": {
    "id": 1,
    "name": "CS101",
    "teacher": {
      "id": 1,
      "name": "Professor Smith",
      "email": "smith@school.edu"
    }
  },
  "face_embeddings_count": 3,
  "consent_given_at": "2026-09-18T09:12:00Z"
}
```

---

## 2. Student Attendance

### `POST /api/attendance/checkin/qr/`
Checks in student using a scanned QR token.

- **Auth Required:** Yes (`Token <token>`)
- **Request Body:**
```json
{
  "qr_token": "valid_qr_token_scanned_from_screen"
}
```
- **Response `201 Created`:**
```json
{
  "message": "Successfully checked in (present).",
  "record": {
    "id": 42,
    "student": 1,
    "student_id": "STU001",
    "student_name": "Alice Johnson",
    "session": 5,
    "class_room_name": "CS101",
    "status": "present",
    "method": "qr",
    "checked_in_at": "2026-09-18T10:05:12Z",
    "confidence_score": null,
    "is_deleted": false,
    "edited_by_username": null
  }
}
```
- **Error Response `400 Bad Request`:**
```json
{
  "error": "QR code has expired. Ask your teacher for a refreshed code."
}
```

---

### `GET /api/attendance/history/`
Returns authenticated student's attendance records.

- **Auth Required:** Yes
- **Response `200 OK`:**
```json
[
  {
    "id": 42,
    "student": 1,
    "student_id": "STU001",
    "student_name": "Alice Johnson",
    "session": 5,
    "class_room_name": "CS101",
    "status": "present",
    "method": "qr",
    "checked_in_at": "2026-09-18T10:05:12Z",
    "confidence_score": null,
    "is_deleted": false,
    "edited_by_username": null
  }
]
```

---

## 3. Face Recognition

### `POST /api/face/enroll/`
Uploads 1 to 5 face photos to enroll biometric templates.

- **Auth Required:** Yes
- **Content-Type:** `multipart/form-data`
- **Body Fields:**
  - `images`: file(s) [supports 1 to 5 image uploads]
  - `student_id`: string *(optional if logged in as student; required if teacher/admin is enrolling)*
- **Response `201 Created`:**
```json
{
  "message": "Successfully enrolled 3 face template(s).",
  "enrolled_count": 3,
  "total_templates": 3,
  "errors": null
}
```

---

### `POST /api/attendance/checkin/face/`
Matches a captured frame and records attendance.

- **Auth Required:** Yes
- **Content-Type:** `multipart/form-data`
- **Body Fields:**
  - `image`: file (camera snapshot)
  - `session_id`: integer (optional, narrows search to this class)
- **Response `200 OK` (Match):**
```json
{
  "matched": true,
  "student_id": "STU001",
  "student_name": "Alice Johnson",
  "confidence_score": 0.8841,
  "attendance": {
    "id": 43,
    "status": "present",
    "method": "face"
  }
}
```
- **Response `200 OK` (No Match):**
```json
{
  "matched": false,
  "score": 0.312,
  "threshold": 0.5,
  "message": "Face not recognized. Please use QR check-in or request teacher assistance."
}
```

---

## 4. Teacher Operations

### `GET /api/teacher/classes/today/`
Returns sessions scheduled today for the authenticated teacher.

- **Auth Required:** Yes (Teacher or Admin)
- **Response `200 OK`:**
```json
[
  {
    "id": 5,
    "class_room": {
      "id": 1,
      "name": "CS101",
      "teacher": { "id": 1, "name": "Professor Smith", "email": "smith@school.edu" }
    },
    "date": "2026-09-18",
    "start_time": "08:00:00",
    "end_time": "10:00:00",
    "qr_token": "token_abc123",
    "qr_token_expires_at": "2026-09-18T08:15:00Z",
    "ended_at": null,
    "is_ended": false,
    "is_qr_valid": true
  }
]
```

---

### `POST /api/teacher/sessions/{id}/qr/`
Generates or rotates a dynamic QR code token with expiry.

- **Auth Required:** Yes (Teacher or Admin)
- **Request Body:**
```json
{
  "expiry_minutes": 10
}
```
- **Response `200 OK`:**
```json
{
  "session_id": 5,
  "qr_token": "u_Yg7rW1bNqV67_K...token...",
  "qr_token_expires_at": "2026-09-18T08:25:00Z",
  "expires_in_minutes": 10
}
```

---

### `POST /api/teacher/sessions/{id}/end/`
Ends session and triggers background absence alerts.

- **Auth Required:** Yes (Teacher or Admin)
- **Response `200 OK`:**
```json
{
  "message": "Session ended. Absence alerts have been triggered.",
  "session_id": 5,
  "ended_at": "2026-09-18T10:00:00Z",
  "task_id": "78b209fa-..."
}
```

---

### `PATCH /api/teacher/attendance/{id}/`
Manual override of an attendance record.

- **Auth Required:** Yes (Teacher or Admin)
- **Request Body:**
```json
{
  "status": "present",
  "is_deleted": false
}
```
- **Response `200 OK`:**
```json
{
  "id": 42,
  "student": 1,
  "student_id": "STU001",
  "status": "present",
  "edited_by_username": "teacher1"
}
```

---

## 5. Reports

### `GET /api/reports/class/{id}/`
- **Response `200 OK`:**
```json
{
  "class_id": 1,
  "class_name": "CS101",
  "teacher": "Professor Smith",
  "total_enrolled_students": 30,
  "total_sessions": 12,
  "total_attendance_records": 340,
  "present_count": 310,
  "present_percentage": 91.2,
  "late_count": 20,
  "late_percentage": 5.9,
  "absent_count": 10,
  "absent_percentage": 2.9
}
```

### `GET /api/reports/student/{id}/`
- **Response `200 OK`:**
```json
{
  "student_id": "STU001",
  "student_name": "Alice Johnson",
  "class_room": "CS101",
  "total_recorded_sessions": 12,
  "present_count": 11,
  "late_count": 1,
  "absent_count": 0,
  "attendance_rate": 91.7
}
```
