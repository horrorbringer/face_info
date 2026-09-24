# Smart Attendance System — Documentation Hub

Welcome to the central documentation index for the Smart Attendance System. This documentation is organized by role and development focus to help our 5-person team collaborate effectively.

---

## 👥 Team Directory & Documentation Guide

| Role | Team Member | Primary Documentation |
| :--- | :--- | :--- |
| **Backend Developers** | Meas Vanny, Sat Chhumseak | [Architecture & Logic Guide](architecture_and_business_logic.md), [API Reference](api_reference.md) |
| **Mobile Developer (Flutter)** | Nov Thearith | [Mobile Build Guide](mobile_build_guide.md), [Mobile API Guide](mobile_api_guide.md), [API Reference](api_reference.md) |
| **UI/UX Designer** | Chhom Rosvisal | [System Capabilities](system_capabilities.md), [Product Specification](smart_attendance_system_spec.md) |
| **Marketing & QA Testing** | Sem Sreyneat | [Testing & QA Guide](testing_guide.md), [System Capabilities](system_capabilities.md) |

---

## 📚 Documentation Catalog

### 1. [Mobile App Build Guide](mobile_build_guide.md) & [Mobile API Guide](mobile_api_guide.md)
> **Summary:** Step-by-step Flutter build setup, camera/hardware permissions, Android/iOS configuration, Dio token interceptors, and end-to-end mobile workflows.
- Flutter dependencies (`mobile_scanner`, `dio`, `camera`, `flutter_secure_storage`).
- Android `AndroidManifest.xml` & iOS `Info.plist` camera permission setup.
- Emulator vs Simulator vs Physical device LAN Base URL resolution.
- Ready-to-use Flutter code templates for QR scanning and Teacher live polling tickers.
- APK and iOS IPA build and distribution commands.

### 2. [System Capabilities](system_capabilities.md)
> **Summary:** The comprehensive feature index and technical capabilities of the platform.
- Multi-classroom student enrollment & teacher course management.
- Multi-teacher authorization (co-teachers / assistant instructors).
- Supported attendance check-in methods (Rotating Dynamic QR code, Vectorized Face matching, Manual bulk overrides, Gate Kiosk).
- Anti-cheat mechanisms (Device anti-hopping, impossible travel velocity guards, campus Wi-Fi IP subnet whitelisting).
- Automated Telegram & Email absence notification flows with non-blocking Celery dispatch.
- Accurate session lifecycle tracking (`started_at`, `ended_at`, `is_cancelled`) with Celery Beat auto-management.
- Multi-angle biometric face enrollment (REST API & Staff Web Camera Studio).
- Teacher scheduling with conflict detection, live polling feeds, and CSV reporting.

### 3. [Architecture & Business Logic Guide](architecture_and_business_logic.md)
> **Summary:** The technical deep-dive for backend engineers and system maintainers.
- High-level system architecture diagrams (Django, Celery, Redis, PostgreSQL, InsightFace).
- Database Entity Relationship Diagram (ERD).
- Sequence flows for QR check-in, Face matching, and asynchronous alert dispatching.
- Configuration variables and production deployment tips.

### 4. [API Reference](api_reference.md), [Mobile Integration Guide](mobile_api_guide.md) & [API Status & MVP Scope](api_status_and_mvp.md)
> **Summary:** Complete REST API endpoint reference, Flutter integration handbook, and verified MVP feature completion scorecard.
- Mobile authentication lifecycle, token revocation on logout, and role routing.
- QR scanner integration, live face check-in, and history pagination.
- Teacher live class roster, roll call, and session scheduling flows.
- Bulk attendance manual overrides and student today schedule endpoints.
- Interactive Swagger UI (`/api/docs/`) and OpenAPI 3.0 schema (`/api/schema/`).
- HTTP error handling matrix and Dart code generator setup.

### 5. [Testing & QA Guide](testing_guide.md)
> **Summary:** Step-by-step procedures for validating new releases.
- Running automated test suites (46 comprehensive test cases passing).
- Manual test checklist for QR check-in, dynamic expiry, and biometric enrollment.
- Testing Celery background tasks and simulated Telegram alerts.
- Audit trail verification.

### 5. [Production Deployment Guide (From Setup 0)](deployment_guide.md)
> **Summary:** Complete production deployment manual covering both **Track A (Docker Compose)** and **Track B (Native Bare-Metal Linux with Systemd, Python venv, PostgreSQL 16+pgvector, and Redis)**, Nginx, Let's Encrypt SSL, and automated backups.

### 6. [Original Product Specification](smart_attendance_system_spec.md)
> **Summary:** The project's requirements, scope boundaries, and development phases.

---

## 🚀 Quick Setup Cheatsheet

### Docker Setup (Recommended for Full Stack with Celery & Redis)
```bash
# 1. Prepare environment
cp .env.example .env

# 2. Build and start services
docker compose up -d --build

# 3. Apply migrations and create superuser
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser

# 4. View logs
docker compose logs -f web celery

# 5. Update running containers after code/template edits
docker compose up -d --build
```

### Local Python Setup (Lightweight SQLite for quick backend testing)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
