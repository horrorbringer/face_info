# Face Info

A consent-first, staff-operated student face lookup pilot. It is **not** attendance or access control. A staff member must confirm every possible match and manual lookup remains available.

## Stack

- Django templates, Bootstrap, and a small browser camera script
- PostgreSQL (the Compose image includes pgvector for production scaling)
- Redis + Celery for background absence notifications
- Optional self-hosted ONNX face-recognition provider

## Documentation

- **[System Capabilities](docs/system_capabilities.md)**: Feature list, check-in methods, background notification workflows, and client integrations.
- **[Architecture & Business Logic Guide](docs/architecture_and_business_logic.md)**: System diagrams, data entity models, end-to-end flows (QR, Face, Absence alerts), API endpoints table, and configurations.
- **[System Specification](docs/smart_attendance_system_spec.md)**: Detailed phase requirements and product scope.


## Prerequisites

- **Python 3.10+** (Django 5.1+ requires Python 3.10 or higher). If on macOS with Homebrew, make sure Homebrew's bin directory is in your `$PATH`.

## How to Run

### Method 1: Run Locally (Fastest - uses local SQLite by default)

When `DATABASE_URL` is omitted, Django defaults to a local SQLite database (`db.sqlite3`), requiring no external database service.

1. **Create and activate a virtual environment:**

   Using standard Python venv:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   Or using `uv`:
   ```bash
   uv venv .venv --python 3.12
   source .venv/bin/activate
   uv pip install -r requirements.txt
   ```

2. **Run database migrations:**
   ```bash
   python manage.py migrate
   ```

3. **Create an initial staff/admin account:**
   ```bash
   python manage.py createsuperuser
   ```

4. **Start the development server:**
   ```bash
   python manage.py runserver
   ```

5. **Access the application:**
   - **Admin portal:** [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/) (log in to create staff users and manage students)
   - **Face Scanner / Kiosk:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
   - **Manual Student Lookup:** [http://127.0.0.1:8000/students/](http://127.0.0.1:8000/students/)

---

### Method 2: Run with Docker Compose (PostgreSQL + pgvector)

1. **Create your environment configuration:**
   ```bash
   cp .env.example .env
   ```
   *(Keep `DJANGO_DEBUG=True` for local HTTP Docker testing; set to `False` only behind HTTPS)*

2. **Build and start services:**
   ```bash
   docker compose up --build
   ```

3. **Apply migrations and create a superuser (in a second terminal):**
   ```bash
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```

4. **Access the application:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

## Important safety and licensing requirements

Face scanning remains disabled unless `FACE_MODEL_PATH` points to an approved model and its Python dependencies are installed. Do not use InsightFace's downloadable recognition weights without confirming their separate licence permits the intended school use. The application never writes raw enrollment or scan images to disk or the database.

Before enrolling anyone: obtain documented consent, assign least-privilege groups, validate matching thresholds against a consented pilot, and maintain manual student-ID lookup.
