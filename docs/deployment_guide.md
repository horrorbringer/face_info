# Production Deployment Guide: From Setup 0 to Launch

This guide provides a comprehensive, end-to-end walkthrough for deploying the **Smart Attendance & Face Info System** to a production Linux server starting from a fresh machine ("Setup 0").

We provide two production-ready deployment pathways:
- **Track A: Docker Compose Deployment (Recommended)** — Complete containerized isolation with pre-configured pgvector and Redis containers.
- **Track B: Native Bare-Metal Linux Deployment (Systemd + Python venv)** — Direct host execution without Docker, delivering maximum CPU throughput for ONNX / InsightFace biometric inference and minimal RAM overhead.

---

## 🏗️ Architecture & Deployment Options Comparison

```text
[ Client Browser / Flutter App ]
              │ (HTTPS / Port 443)
              ▼
    [ Nginx Reverse Proxy ] (SSL Termination & Static Assets)
              │ (HTTP / Port 8000)
              ▼
  ┌────────────────────────────────────────────────────────┐
  │ Option A: Docker Compose Stack                         │
  │   - web: gunicorn in python:3.12-slim                  │
  │   - db: pgvector/pgvector:pg16 container               │
  │   - redis: redis:7-alpine container                    │
  │   - celery: celery worker container                    │
  └────────────────────────────────────────────────────────┘
                          — OR —
  ┌────────────────────────────────────────────────────────┐
  │ Option B: Native Bare-Metal Stack (Systemd)            │
  │   - faceinfo-web.service (Gunicorn in Python 3.12 venv)│
  │   - postgresql.service (Native PostgreSQL 16+pgvector) │
  │   - redis-server.service (Native Redis 7)              │
  │   - faceinfo-celery.service (Native Celery Worker)     │
  └────────────────────────────────────────────────────────┘
```

### Which Option Should You Choose?

| Factor | Track A: Docker Compose | Track B: Native Bare-Metal |
| :--- | :--- | :--- |
| **Best For** | Standard production, teams wanting container isolation | Maximum ONNX/CPU performance, servers with limited RAM |
| **Setup Complexity** | Lower (everything bundled in Compose) | Moderate (manual setup of PostgreSQL, Redis, Systemd) |
| **RAM Overhead** | ~400–600 MB base container overhead | ~150–250 MB (very lightweight) |
| **Biometric Speed** | Standard Docker virtualized CPU instructions | 5–15% faster native AVX-512 / OpenMP CPU throughput |
| **Maintenance** | `docker compose up -d --build` | `git pull && pip install && systemctl restart` |

> [!IMPORTANT]
> **HTTPS is Strictly Required for Face Scanning & Camera Access**:
> Web browsers block camera access (`navigator.mediaDevices.getUserMedia`) on all non-secure origins except `localhost`. You **must** configure a domain name and an SSL/TLS certificate (e.g. Let's Encrypt) for camera enrollment and kiosk lookup to work on production servers.

---

## 📋 Minimum Server Specifications

| Resource | Minimum | Recommended (with Biometric ONNX Models) |
| :--- | :--- | :--- |
| **Operating System** | Ubuntu 22.04 / 24.04 LTS | Ubuntu 24.04 LTS (x86_64) |
| **CPU** | 2 vCPU | 4 vCPU (AVX2 / AVX-512 supported) |
| **RAM** | 2 GB | 4 GB - 8 GB |
| **Storage** | 20 GB SSD | 40 GB+ SSD |
| **Network** | Public Static IPv4 | Public Static IPv4 + Domain Name (`attendance.domain.com`) |

---

## Step 1: Server Provisioning & Initial Hardening (Common to Both)

Log in to your fresh server via SSH as `root`:
```bash
ssh root@YOUR_SERVER_IP
```

### 1.1 Update Base Packages
```bash
apt update && apt upgrade -y
apt install -y curl wget git ufw htop fail2ban unzip ca-certificates gnupg lsb-release
```

### 1.2 Create a Non-Root Deployment User
```bash
# Create user 'deploy'
adduser --gecos "" deploy

# Grant sudo privileges
usermod -aG sudo deploy

# Copy authorized SSH keys from root to deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

### 1.3 Configure Firewall (UFW)
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

### 1.4 Domain & DNS Setup
Point your domain's DNS `A` record to your server's public IP:
- **Type:** `A`
- **Host:** `attendance` (or `@` for root domain)
- **Value:** `YOUR_SERVER_IP`
- **TTL:** 300 (or Auto)

Example: `attendance.yourdomain.com`

---

# 🐳 Track A: Docker Compose Deployment

*If you prefer native bare-metal deployment without Docker, skip to [Track B: Native Bare-Metal Deployment](#-track-b-native-bare-metal-deployment).*

### A.1 Install Docker Engine & Compose
Switch to the `deploy` user:
```bash
su - deploy
```

Install Docker official repository:
```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

### A.2 Clone & Configure Environment
```bash
cd /home/deploy
git clone https://github.com/YOUR_ORGANIZATION/face_info.git
cd face_info

cp .env.example .env
nano .env
```

Set production configuration:
```ini
DJANGO_SECRET_KEY=GENERATE_WITH_PYTHON_SECRETS
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=attendance.yourdomain.com,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://attendance.yourdomain.com

DATABASE_URL=postgresql://faceinfo:YOUR_SECURE_PASSWORD@db:5432/faceinfo
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

MATCH_THRESHOLD=0.5
LATE_THRESHOLD_MINUTES=15
```

Align the database password in `docker-compose.yml` under `POSTGRES_PASSWORD`.

### A.3 Build & Start Docker Stack
```bash
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_roles_and_users --reset-passwords
```

Proceed directly to [Step 8: Nginx Reverse Proxy & SSL](#step-8-nginx-reverse-proxy--ssl-lets-encrypt).

---

# ⚡ Track B: Native Bare-Metal Deployment

Deploying directly on the host Linux system without Docker containers.

### B.1 Install Native Runtime Dependencies
Switch to the `deploy` user:
```bash
su - deploy
```

Install Python 3.12, virtual environment tools, C/C++ build toolchains, and OpenCV hardware acceleration libraries:
```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev build-essential \
    libpq-dev libglib2.0-0 libgl1 libgomp1 libxcb1 libxext6 libxrender1 \
    pkg-config libopenblas-dev
```

### B.2 Install & Configure Native PostgreSQL 16 with pgvector
Add the official PostgreSQL repository for Ubuntu:
```bash
sudo install -d /etc/apt/keyrings
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg
echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list

sudo apt update
sudo apt install -y postgresql-16 postgresql-server-dev-16 postgresql-16-pgvector

sudo systemctl enable --now postgresql
```

Create the dedicated database, user, and activate the `vector` extension:
```bash
# Generate a strong password:
DB_PASS=$(python3 -c "import secrets; print(secrets.token_hex(16))")
echo "Database Password: $DB_PASS"

# Create user & database in PostgreSQL:
sudo -u postgres psql -c "CREATE USER faceinfo WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "CREATE DATABASE faceinfo OWNER faceinfo;"
sudo -u postgres psql -d faceinfo -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Verify pgvector extension is installed:
sudo -u postgres psql -d faceinfo -c "\dx"
```

### B.3 Install & Configure Native Redis Server
```bash
sudo apt install -y redis-server
sudo systemctl enable --now redis-server

# Verify Redis is responsive:
redis-cli ping
# Expected output: PONG
```

### B.4 Clone Repository & Setup Virtual Environment
```bash
cd /home/deploy
git clone https://github.com/YOUR_ORGANIZATION/face_info.git
cd face_info

# Create Python 3.12 virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Upgrade pip and install all production packages
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# Create dedicated runtime logging and staticfiles directories
mkdir -p logs staticfiles
```

### B.5 Configure Production `.env` for Native Host
```bash
cp .env.example .env
nano .env
```

Configure connection strings pointing to local native services (`127.0.0.1`):
```ini
# --- Django Core Settings ---
DJANGO_SECRET_KEY=YOUR_GENERATED_SECRET_KEY
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=attendance.yourdomain.com,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://attendance.yourdomain.com

# --- Native PostgreSQL 16 (Localhost) ---
DATABASE_URL=postgresql://faceinfo:YOUR_DB_PASSWORD@127.0.0.1:5432/faceinfo

# --- Native Redis 7 (Localhost) ---
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# --- Biometric Face Recognition Configuration ---
# Path to ONNX model directory on the host (leave empty if unconfigured)
FACE_MODEL_PATH=/home/deploy/face_info/models/approved_pack
MATCH_THRESHOLD=0.5
LATE_THRESHOLD_MINUTES=15

# --- Absence Notification Alerts ---
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
```

### B.6 Initialize Database, Seed Accounts & Collect Static Assets
With virtual environment active (`source venv/bin/activate`):
```bash
# 1. Run database migrations
python manage.py migrate

# 2. Seed groups, permissions, classrooms, and test accounts
python manage.py seed_roles_and_users --reset-passwords

# 3. Compile and gather static assets for Nginx
python manage.py collectstatic --noinput

# 4. Run automated test suite to verify native environment
python manage.py test
```

### B.7 Configure Systemd Services (Gunicorn & Celery)

Create two Systemd unit files to manage Gunicorn and Celery as reliable background daemons that restart automatically on server boot or failure.

#### 1. Gunicorn Web Service (`/etc/systemd/system/faceinfo-web.service`)
```bash
sudo vim /etc/systemd/system/faceinfo-web.service
```
Paste:
```ini
[Unit]
Description=Smart Attendance Gunicorn Web Server
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/face_info
EnvironmentFile=/home/ubuntu/face_info/.env
ExecStart=/home/ubuntu/face_info/venv/bin/gunicorn config.wsgi:application \
          --bind 127.0.0.1:8000 \
          --workers 3 \
          --threads 4 \
          --worker-class gthread \
          --worker-tmp-dir /dev/shm \
          --timeout 120 \
          --graceful-timeout 30 \
          --keep-alive 65 \
          --max-requests 1000 \
          --max-requests-jitter 100 \
          --access-logfile /home/ubuntu/face_info/logs/gunicorn-access.log \
          --error-logfile /home/ubuntu/face_info/logs/gunicorn-error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 2. Celery Worker Service (`/etc/systemd/system/faceinfo-celery.service`)
```bash
sudo nano /etc/systemd/system/faceinfo-celery.service
```
Paste:
```ini
[Unit]
Description=Smart Attendance Celery Background Worker
After=network.target redis-server.service postgresql.service
Requires=redis-server.service postgresql.service

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/face_info
EnvironmentFile=/home/ubuntu/face_info/.env
ExecStart=/home/ubuntu/face_info/venv/bin/celery -A config worker -l info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 3. Enable and Start Native Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now faceinfo-web.service faceinfo-celery.service

# Verify both services are active (running):
sudo systemctl status faceinfo-web.service --no-pager
sudo systemctl status faceinfo-celery.service --no-pager
```

---

## Step 8: Nginx Reverse Proxy & SSL (Let's Encrypt)

Nginx handles HTTPS termination and proxies traffic to port `8000`.

### 8.1 Install Nginx & Certbot
```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 8.2 Create Nginx Server Block
```bash
sudo nano /etc/nginx/sites-available/face_info
```

Paste the following configuration (replace `student-attendance.vanny.monster` with your domain):

```nginx
upstream faceinfo_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name student-attendance.vanny.monster;

    # Maximum file upload size for multi-angle face pictures
    client_max_body_size 25M;

    # In Native Deployment: Nginx serves staticfiles directly from disk for peak speed
    location /static/ {
        alias /home/ubuntu/face_info/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }

    location / {
        proxy_pass http://faceinfo_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Transparently retry on backend connection drops/resets to avoid 502 Bad Gateway
        proxy_next_upstream error timeout invalid_header http_502 http_503 http_504;
        proxy_next_upstream_tries 3;

        # Timeouts: Extended to prevent Cloudflare Error 524 timeouts
        proxy_connect_timeout 120s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;

        # Buffers: Prevent Cloudflare Error 520 (upstream buffer overflow on large headers)
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;
    }
}
```

> [!WARNING]
> **Crucial Ubuntu Permission Step**:
> On Ubuntu, the `/home/ubuntu` directory has permissions `750` or `700`, which blocks Nginx (`www-data` user) from accessing `/home/ubuntu/face_info/staticfiles/` and causes **`HTTP 403 Forbidden`** on all CSS/JS files.
> You **must** grant execute permissions so Nginx can traverse the path:
> ```bash
> chmod 755 /home/ubuntu
> chmod -R 755 /home/ubuntu/face_info/staticfiles
> ```

### 8.3 Enable Site & Obtain SSL Certificate
```bash
sudo ln -s /etc/nginx/sites-available/face_info /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# Issue Let's Encrypt SSL:
sudo certbot --nginx -d student-attendance.vanny.monster
```

Certbot will automatically install SSL certificates and configure HTTPS redirects.

---

## Step 9: Post-Deployment Verification Checklist

Verify the entire system live:
1. **HTTPS Padlock**: Navigate to `https://attendance.yourdomain.com/` — confirm certificate is valid.
2. **API Discovery & Healthcheck**:
   - `https://attendance.yourdomain.com/api/` — returns `status: "operational"`
   - `https://attendance.yourdomain.com/api/health/` — returns `status: "healthy"`
3. **Interactive Swagger Console**: Open `https://attendance.yourdomain.com/api/docs/` and test login.
4. **Staff Camera Viewfinder**: Open `https://attendance.yourdomain.com/recognition/kiosk/` and ensure camera access is granted.
5. **Background Alerts**: Check Celery logs to ensure background tasks are ready:
   - *Docker:* `docker compose logs -f celery`
   - *Native:* `sudo journalctl -u faceinfo-celery.service -f`

---

## Step 10: Day-2 Maintenance & Automated Backups

### 10.1 How to Apply Application Updates

#### For Track A (Docker):
```bash
cd /home/deploy/face_info
git pull origin main
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
```

#### For Track B (Native Bare-Metal):
```bash
cd /home/deploy/face_info
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart faceinfo-web.service faceinfo-celery.service
```

### 10.2 Continuous Deployment with GitHub Actions

The repository includes a ready-to-use GitHub Actions workflow in [`.github/workflows/deploy.yml`](file:///.github/workflows/deploy.yml) that executes on every push to `main`:
1. **Automated Test Suite**: Boots Python 3.12, installs system libraries (`libgl1`, `libglib2.0-0`), installs `requirements.txt`, and executes `python manage.py test`.
2. **Automated SSH Deployment**: Connects to your production Ubuntu server, pulls latest commits, runs migrations, compiles static assets with proper permissions, and restarts `faceinfo-web` and `faceinfo-celery` systemd services.

#### 1. Configure Passwordless Systemctl for CI/CD User
To allow the deployment script to restart the background services without hanging on a sudo password prompt, create a sudoers rule:
```bash
echo "ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl restart faceinfo-web.service, /bin/systemctl restart faceinfo-celery.service, /bin/systemctl reload nginx" | sudo tee /etc/sudoers.d/faceinfo
sudo chmod 0440 /etc/sudoers.d/faceinfo
```

#### 2. Configure GitHub Repository Secrets
In your GitHub repository, go to **Settings** > **Secrets and variables** > **Actions** and add:
- `PROD_SSH_HOST`: Your server IP or domain (e.g. `student-attendance.vanny.monster`)
- `PROD_SSH_USER`: SSH user on the server (e.g. `ubuntu`)
- `PROD_SSH_KEY`: The private SSH key (e.g. content of `~/.ssh/id_rsa` or your deployment private key)
- `PROD_SSH_PORT`: (Optional, defaults to `22`)

---

### 10.3 Automated Daily PostgreSQL Database Backups
Create a backup script:
```bash
mkdir -p /home/ubuntu/backups
nano /home/ubuntu/backup_db.sh
```

Paste (works for both Docker and Native):
```bash
#!/bin/bash
BACKUP_DIR="/home/ubuntu/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
FILENAME="$BACKUP_DIR/faceinfo_db_$TIMESTAMP.sql.gz"

# For Track B (Native):
pg_dump -U faceinfo -h 127.0.0.1 faceinfo | gzip > "$FILENAME"

# (For Track A Docker, use instead:
# docker compose -f /home/deploy/face_info/docker-compose.yml exec -T db pg_dump -U faceinfo faceinfo | gzip > "$FILENAME" )

# Keep only the last 14 days of backups
find "$BACKUP_DIR" -name "faceinfo_db_*.sql.gz" -type f -mtime +14 -delete
```

Make executable and add to crontab:
```bash
chmod +x /home/ubuntu/backup_db.sh
crontab -e
# Add line to run every day at 2:00 AM:
0 2 * * * /home/ubuntu/backup_db.sh >> /home/ubuntu/backups/backup.log 2>&1
```

---

## 🛠️ Common Troubleshooting

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **Camera permission denied / Not allowed** | Domain accessed over HTTP or camera blocked | Access via `https://` with valid SSL; check browser permission settings. |
| **CSRF verification failed (HTTP 403)** | `CSRF_TRUSTED_ORIGINS` mismatch | Add your exact domain including `https://` to `CSRF_TRUSTED_ORIGINS` in `.env` and restart. |
| **502 Bad Gateway** | Gunicorn service or web container down | Native: `sudo systemctl status faceinfo-web.service` / Docker: `docker compose logs web`. |
| **pgvector extension error** | Extension not enabled in PostgreSQL | Run `sudo -u postgres psql -d faceinfo -c "CREATE EXTENSION IF NOT EXISTS vector;"`. |
| **OpenCV ImportError: libGL.so.1** | Missing native graphics libraries | Run `sudo apt install -y libgl1 libglib2.0-0`. |
| **CSS / Static files not loading (HTTP 403)** | Nginx (`www-data`) lacks traverse permissions on `/home/ubuntu` | Run `chmod 755 /home/ubuntu` and `chmod -R 755 /home/ubuntu/face_info/staticfiles`. |
| **Celery alerts not sending** | Telegram token missing or invalid Chat ID | Check `TELEGRAM_BOT_TOKEN` in `.env` and review Celery logs. |
