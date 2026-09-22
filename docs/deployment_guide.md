# Production Deployment Guide: From Setup 0 to Launch

This guide provides a comprehensive, end-to-end walkthrough for deploying the **Smart Attendance & Face Info System** to a production server starting from a blank Linux machine ("Setup 0").

---

## 🏗️ Architecture Overview

In a production environment, the system runs with the following components:

```text
[ Client Browser / Mobile App ]
              │ (HTTPS / Port 443)
              ▼
    [ Nginx Reverse Proxy ] (SSL Termination & Static Headers)
              │ (HTTP / Port 8000)
              ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │ Docker Compose Network                                          │
   │                                                                 │
   │  [ Web Container ] ── Gunicorn (Django 5.1, Whitenoise)         │
   │         │                                                       │
   │         ├──► [ Database Container ] ── PostgreSQL 16 + pgvector │
   │         │                                                       │
   │         └──► [ Redis Container ] ──── Cache & Message Broker    │
   │                     ▲                                           │
   │  [ Celery Container ] ── Async Task Worker (Absence Alerts)     │
   └─────────────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **HTTPS is Strictly Required for Face Scanning & Camera Access**:
> Web browsers (Chrome, Safari, Firefox, Edge) block webcam access (`navigator.mediaDevices.getUserMedia`) on all non-secure origins except `localhost`. You **must** configure a domain name and an SSL/TLS certificate (e.g. Let's Encrypt) for camera enrollment and kiosk lookup to work on production servers.

---

## 📋 Minimum Server Specifications

| Resource | Minimum | Recommended (with ONNX Face Recognition) |
| :--- | :--- | :--- |
| **Operating System** | Ubuntu 22.04 / 24.04 LTS | Ubuntu 24.04 LTS (x86_64) |
| **CPU** | 2 vCPU | 4 vCPU |
| **RAM** | 2 GB | 4 GB - 8 GB |
| **Storage** | 20 GB SSD | 40 GB+ SSD |
| **Network** | Public Static IPv4 | Public Static IPv4 + Domain Name |

---

## Step 1: Server Provisioning & Initial Hardening (Setup 0)

Log in to your fresh server via SSH as `root`:

```bash
ssh root@YOUR_SERVER_IP
```

### 1.1 Update Base Packages
```bash
apt update && apt upgrade -y
apt install -y curl wget git ufw htop fail2ban unzip ca-certificates gnupg
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
Allow only essential ports (SSH, HTTP, HTTPS):
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

---

## Step 2: Install Docker Engine & Docker Compose

Switch to the `deploy` user or remain in a terminal with sudo access:

```bash
su - deploy
```

### 2.1 Install Docker via Official Repository
```bash
# Add Docker's official GPG key:
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add Docker repository:
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### 2.2 Configure Docker Permissions
```bash
sudo usermod -aG docker $USER
# Apply the new group without logging out:
newgrp docker
# Test Docker installation:
docker run --rm hello-world
```

---

## Step 3: Domain & DNS Configuration

Before setting up SSL, point your domain's DNS `A` record to your server's public IP address:

```text
Type: A
Host: attendance (or @ for root domain)
Value: YOUR_SERVER_IP
TTL: 300 (or Auto)
```

Example domain: `attendance.yourdomain.com`

---

## Step 4: Clone Repository & Configure Environment

### 4.1 Clone Repository
```bash
cd /home/deploy
git clone https://github.com/YOUR_ORGANIZATION/face_info.git
cd face_info
```

### 4.2 Generate Cryptographically Strong Secrets
Run this quick Python one-liner to generate a secret key:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```
Also generate a secure database password:
```bash
python3 -c "import secrets; print(secrets.token_hex(16))"
```

### 4.3 Configure Production `.env`
Create `.env` from the example template:
```bash
cp .env.example .env
nano .env
```

Set the production variables:

```ini
# --- Django Core Settings ---
DJANGO_SECRET_KEY=PASTE_GENERATED_SECRET_KEY_HERE
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=attendance.yourdomain.com,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://attendance.yourdomain.com

# --- Database Settings ---
# Format: postgresql://<user>:<password>@db:5432/<dbname>
DATABASE_URL=postgresql://faceinfo:YOUR_STRONG_DB_PASSWORD@db:5432/faceinfo

# --- Celery & Redis Settings ---
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# --- Biometric Face Recognition (Optional) ---
# Path inside Docker container to approved model directory; leave blank if unconfigured
FACE_MODEL_PATH=
MATCH_THRESHOLD=0.5
LATE_THRESHOLD_MINUTES=15

# --- Absence Notification Alerts ---
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_OPTIONAL
```

### 4.4 Align Database Password in `docker-compose.yml`
Open `docker-compose.yml` and ensure the database service uses your chosen password:
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: faceinfo
      POSTGRES_USER: faceinfo
      POSTGRES_PASSWORD: YOUR_STRONG_DB_PASSWORD
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

---

## Step 5: (Optional) Install Face Recognition ONNX Model

If you have an approved face recognition model pack (e.g. InsightFace model pack directory with `.onnx` weights):

1. Create a `models/` directory in the project root:
   ```bash
   mkdir -p models/approved_pack
   ```
2. Copy your approved ONNX files into `models/approved_pack/`.
3. In `docker-compose.yml`, mount the models directory into the `web` and `celery` containers:
   ```yaml
   web:
     volumes:
       - ./models:/app/models:ro
   celery:
     volumes:
       - ./models:/app/models:ro
   ```
4. Set in `.env`:
   ```ini
   FACE_MODEL_PATH=/app/models/approved_pack
   ```

---

## Step 6: Build & Launch Docker Services

### 6.1 Build and Start Containers
```bash
docker compose up -d --build
```

### 6.2 Check Container Status
```bash
docker compose ps
```
You should see 4 healthy running containers:
- `face_info-web-1` (running Gunicorn on port 8000)
- `face_info-celery-1` (running Celery worker)
- `face_info-db-1` (PostgreSQL 16 + pgvector)
- `face_info-redis-1` (Redis 7)

### 6.3 Apply Database Migrations
```bash
docker compose exec web python manage.py migrate
```

### 6.4 Create Administrative Superuser
```bash
docker compose exec web python manage.py createsuperuser
```
Follow the interactive prompts to create your primary administrator credentials.

---

## Step 7: Nginx Reverse Proxy & SSL (Let's Encrypt)

Now we expose the app securely to the world via Nginx on port 443 with Let's Encrypt SSL.

### 7.1 Install Nginx & Certbot
```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 7.2 Create Nginx Server Block
Create `/etc/nginx/sites-available/face_info`:
```bash
sudo nano /etc/nginx/sites-available/face_info
```

Paste the following configuration (replace `attendance.yourdomain.com` with your actual domain):

```nginx
server {
    listen 80;
    server_name attendance.yourdomain.com;

    # Maximum file upload size (allows multi-frame camera enrollment)
    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket / Timeout buffer settings
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }
}
```

### 7.3 Enable the Site & Test Configuration
```bash
sudo ln -s /etc/nginx/sites-available/face_info /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 7.4 Obtain Free SSL Certificate via Certbot
```bash
sudo certbot --nginx -d attendance.yourdomain.com
```
Follow the interactive prompts (enter your email and agree to terms). Certbot will automatically configure HTTPS redirect and SSL certificates.

### 7.5 Verify SSL Auto-Renewal
```bash
sudo certbot renew --dry-run
```

---

## Step 8: Post-Deployment Verification Checklist

Open your browser and verify:

1. **HTTPS Connectivity**: Navigate to `https://attendance.yourdomain.com/` — confirm the padlock appears.
2. **Admin Portal**: Go to `https://attendance.yourdomain.com/admin/` and log in with the superuser created in Step 6.4.
3. **Staff Camera Kiosk**: Open `https://attendance.yourdomain.com/` and ensure the browser prompts for camera permissions.
4. **Student Enrollment Studio**:
   - Go to `https://attendance.yourdomain.com/students/` (Manual Lookup).
   - Create a student in Admin or CSV import.
   - Click "Enroll" (`https://attendance.yourdomain.com/students/<id>/enroll/`).
   - Verify the webcam stream loads inside the oval guide and snapshot capture works.
5. **Background Workers (Celery & Redis)**:
   ```bash
   docker compose logs -f celery
   ```
   Confirm Celery reports `celery@... ready` and lists `send_absence_alerts_for_session`.

---

## Step 9: Day-2 Maintenance & Operations

### 9.1 How to Apply Application Updates
Whenever new code or templates are pushed to Git:

```bash
cd /home/deploy/face_info

# 1. Pull latest changes
git pull origin main

# 2. Rebuild and restart containers in background
docker compose up -d --build

# 3. Apply any new migrations
docker compose exec web python manage.py migrate

# 4. View logs to confirm clean startup
docker compose logs --tail=50 -f web celery
```

### 9.2 Automated PostgreSQL Database Backups
Create an automated daily backup script:

```bash
mkdir -p /home/deploy/backups
nano /home/deploy/backup_db.sh
```

Paste:
```bash
#!/bin/bash
BACKUP_DIR="/home/deploy/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
FILENAME="$BACKUP_DIR/faceinfo_db_$TIMESTAMP.sql.gz"

docker compose -f /home/deploy/face_info/docker-compose.yml exec -T db pg_dump -U faceinfo faceinfo | gzip > "$FILENAME"

# Keep only the last 14 days of backups
find "$BACKUP_DIR" -name "faceinfo_db_*.sql.gz" -type f -mtime +14 -delete
```

Make executable and add to crontab:
```bash
chmod +x /home/deploy/backup_db.sh

# Open crontab:
crontab -e
# Add line to run every night at 2:00 AM:
0 2 * * * /home/deploy/backup_db.sh >> /home/deploy/backups/backup.log 2>&1
```

### 9.3 Restoring a Database Backup
```bash
gunzip < /home/deploy/backups/faceinfo_db_YYYYMMDD_HHMMSS.sql.gz | docker compose exec -T db psql -U faceinfo -d faceinfo
```

---

## 🛠️ Common Troubleshooting

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **Camera permission denied / Not allowed** | Domain accessed over HTTP or camera blocked | Access via `https://` with valid SSL; check browser permission settings. |
| **CSRF verification failed (HTTP 403)** | `CSRF_TRUSTED_ORIGINS` mismatch | Add your exact domain including `https://` to `CSRF_TRUSTED_ORIGINS` in `.env` and restart. |
| **502 Bad Gateway** | Web container not running or crashed | Run `docker compose logs web` to diagnose traceback. |
| **"Face scanning is not configured"** | `FACE_MODEL_PATH` empty or invalid | Confirm model files exist and the volume mount is active in `docker-compose.yml`. |
| **Celery alerts not sending** | Telegram token missing or invalid Chat ID | Check `TELEGRAM_BOT_TOKEN` in `.env` and review `docker compose logs celery`. |
