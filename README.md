# Face Info

A consent-first, staff-operated student face lookup pilot. It is **not** attendance or access control. A staff member must confirm every possible match and manual lookup remains available.

## Stack

- Django templates, Bootstrap, and a small browser camera script
- PostgreSQL (the Compose image includes pgvector for production scaling)
- Optional self-hosted ONNX face-recognition provider

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/` to create staff users and import students. For containers, copy `.env.example` to `.env`, set a secret, then run `docker compose up --build`. Keep `DJANGO_DEBUG=True` for local HTTP Docker use; set it to `False` only when the deployed service is behind HTTPS.

## Important safety and licensing requirements

Face scanning remains disabled unless `FACE_MODEL_PATH` points to an approved model and its Python dependencies are installed. Do not use InsightFace's downloadable recognition weights without confirming their separate licence permits the intended school use. The application never writes raw enrollment or scan images to disk or the database.

Before enrolling anyone: obtain documented consent, assign least-privilege groups, validate matching thresholds against a consented pilot, and maintain manual student-ID lookup.
