# Smart Attendance System — Documentation Hub

Welcome to the central documentation index for the Smart Attendance System. This documentation is organized by role and development focus to help our 5-person team collaborate effectively.

---

## 👥 Team Directory & Documentation Guide

| Role | Team Member | Primary Documentation |
| :--- | :--- | :--- |
| **Backend Developers** | Meas Vanny, Sat Chhumseak | [Architecture & Logic Guide](architecture_and_business_logic.md), [API Reference](api_reference.md) |
| **Mobile Developer (Flutter)** | Nov Thearith | [Mobile API Guide](mobile_api_guide.md), [API Reference](api_reference.md) |
| **UI/UX Designer** | Chhom Rosvisal | [System Capabilities](system_capabilities.md), [Product Specification](smart_attendance_system_spec.md) |
| **Marketing & QA Testing** | Sem Sreyneat | [Testing & QA Guide](testing_guide.md), [System Capabilities](system_capabilities.md) |

---

## 📚 Documentation Catalog

### 1. [System Capabilities](system_capabilities.md)
> **Summary:** The feature index of what the platform can currently do.
- Supported attendance check-in methods (QR code, Face matching, manual override).
- Automated Telegram & Email absence notification flows.
- Multi-angle biometric face enrollment (REST API & Staff Web Camera Studio).
- Teacher scheduling and class analytics.

### 2. [Architecture & Business Logic Guide](architecture_and_business_logic.md)
> **Summary:** The technical deep-dive for backend engineers and system maintainers.
- High-level system architecture diagrams (Django, Celery, Redis, PostgreSQL, InsightFace).
- Database Entity Relationship Diagram (ERD).
- Sequence flows for QR check-in, Face matching, and asynchronous alert dispatching.
- Configuration variables and production deployment tips.

### 3. [API Reference](api_reference.md) & [Mobile Integration Guide](mobile_api_guide.md)
> **Summary:** Complete REST API endpoint reference and step-by-step Flutter integration handbook.
- Mobile authentication lifecycle, token revocation on logout, and role routing.
- QR scanner integration, live face check-in, and history pagination.
- Teacher live class roster, roll call, and session scheduling flows.
- Interactive Swagger UI (`/api/docs/`) and OpenAPI 3.0 schema (`/api/schema/`).
- HTTP error handling matrix and Dart code generator setup.

### 4. [Testing & QA Guide](testing_guide.md)
> **Summary:** Step-by-step procedures for validating new releases.
- Running automated test suites (19 test cases).
- Manual test checklist for QR check-in, dynamic expiry, and biometric enrollment.
- Testing Celery background tasks and simulated Telegram alerts.
- Audit trail verification.

### 5. [Production Deployment Guide (From Setup 0)](deployment_guide.md)
> **Summary:** Complete production deployment manual from server provisioning, firewall, Docker, Nginx, Let's Encrypt SSL, to automated backups.

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
