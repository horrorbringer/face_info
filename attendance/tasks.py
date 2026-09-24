import datetime
import logging
import secrets
import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone
from .models import AlertLog, AttendanceRecord, Session

logger = logging.getLogger(__name__)


_telegram_session = None


def _get_telegram_session():
    global _telegram_session
    if _telegram_session is None:
        _telegram_session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
        _telegram_session.mount("https://", adapter)
        _telegram_session.mount("http://", adapter)
    return _telegram_session


def send_telegram_alert(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    sess = _get_telegram_session()
    resp = sess.post(url, json=payload, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description')}")
    return data


def send_email_alert(recipient_email, student_name, session_name, date_str):
    subject = f"Absence Notice: {student_name} - {date_str}"
    message = (
        f"Dear Guardian,\n\n"
        f"This is an automated notification that {student_name} was marked absent for "
        f"session {session_name} on {date_str}.\n\n"
        f"If this is unexpected, please contact the school office.\n"
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "attendance@school.edu")
    send_mail(subject, message, from_email, [recipient_email], fail_silently=False)


@shared_task(bind=True, max_retries=3)
def send_absence_alerts_for_session(self, session_id):
    """
    Idempotent absence-alert task for ended sessions.
    Finds active students in the class with no AttendanceRecord,
    marks them absent, and dispatches Telegram/email alerts with full audit logging.
    """
    try:
        session = Session.objects.select_related("class_room").get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session with id {session_id} not found.")
        return {"error": "Session not found"}

    if session.is_cancelled_or_inactive:
        logger.info(f"Skipping absence alerts for cancelled session {session_id}.")
        return {"cancelled": True, "message": "Session was cancelled"}

    # Get all active students enrolled in this session's class (via classrooms M2M or primary class_room)
    students = session.class_room.get_enrolled_students(active_only=True)
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")

    sent_count = 0
    failed_count = 0

    for student in students:
        # Check if attendance was already recorded for this session
        record, created = AttendanceRecord.objects.get_or_create(
            student=student,
            session=session,
            defaults={"status": "absent", "method": "system", "checked_in_at": None}
        )

        # Only alert for absent students
        if record.status != "absent":
            continue

        # Idempotency check: Don't re-send if already logged as 'sent' for this student & session
        if AlertLog.objects.filter(student=student, session=session, status="sent").exists():
            continue

        contact = (student.guardian_contact or "").strip()
        channel = "none"
        error_msg = ""
        success = False

        message_text = (
            f"⚠️ <b>Attendance Alert</b>\n"
            f"Student: <b>{student.full_name}</b> ({student.student_id})\n"
            f"Class: {session.class_room.name}\n"
            f"Date: {session.date.strftime('%Y-%m-%d')}\n"
            f"Status: <b>Absent</b> without prior notice."
        )

        # Determine notification channel: Telegram chat_id or Email
        tg_id = student.effective_guardian_telegram_id
        email_addr = student.effective_guardian_email

        if tg_id:
            # Valid numerical chat_id (e.g. 584930192 or -100123456789)
            channel = "telegram"
            if not bot_token:
                error_msg = "TELEGRAM_BOT_TOKEN is not configured in settings."
            else:
                try:
                    send_telegram_alert(bot_token, tg_id, message_text)
                    success = True
                except Exception as exc:
                    error_msg = str(exc)
                    logger.warning(f"Telegram send failed for {student.student_id}: {exc}")
        elif email_addr:
            # Email format
            channel = "email"
            try:
                send_email_alert(email_addr, student.full_name, session.class_room.name, str(session.date))
                success = True
            except Exception as exc:
                error_msg = str(exc)
                logger.warning(f"Email send failed for {student.student_id}: {exc}")
        elif contact.startswith("@"):
            # Telegram Bot API cannot initiate chats with @usernames directly
            channel = "telegram"
            error_msg = (
                f"Telegram requires a numeric chat_id. Cannot send to username '{contact}'. "
                f"Parent must link account via Telegram bot first."
            )
            logger.warning(f"Telegram skipped for {student.student_id}: {error_msg}")
        elif contact:
            channel = "unsupported"
            error_msg = f"Unknown contact format: '{contact}'"
        else:
            channel = "none"
            error_msg = "No guardian contact information on file."

        status_str = "sent" if success else "failed"
        AlertLog.objects.create(
            student=student,
            session=session,
            channel=channel,
            status=status_str,
            error_message=error_msg
        )

        if success:
            sent_count += 1
        else:
            failed_count += 1

    return {
        "session_id": session_id,
        "sent_count": sent_count,
        "failed_count": failed_count,
    }


def sync_sessions_lifecycle(session_qs=None):
    """
    Synchronizes session schedule states with current clock time:
    1. Auto-Start:
       - If session.date == today and start_time <= now < end_time and ended_at is None:
         - Ensures qr_token exists and qr_token_expires_at covers through end_time.
    2. Auto-End:
       - If session.ended_at is None AND (session.date < today OR (session.date == today and now >= end_time)):
         - Sets ended_at = now
         - Triggers send_absence_alerts_for_session for unrecorded students.
    """
    now = timezone.now()
    today = timezone.localdate()

    if session_qs is None:
        session_qs = Session.objects.filter(
            Q(date=today) | Q(date__lt=today, ended_at__isnull=True)
        ).select_related("class_room")

    started_count = 0
    ended_count = 0

    for s in session_qs:
        # Skip cancelled sessions
        if s.is_cancelled_or_inactive:
            continue

        s_end_dt = timezone.make_aware(
            datetime.datetime.combine(s.date, s.end_time),
            timezone.get_current_timezone()
        )
        s_start_dt = timezone.make_aware(
            datetime.datetime.combine(s.date, s.start_time),
            timezone.get_current_timezone()
        )

        # 1. Auto-End check
        if s.ended_at is None:
            if s.date < today or now >= s_end_dt:
                s.ended_at = now
                s.save(update_fields=["ended_at"])
                ended_count += 1
                logger.info(f"Auto-ended session {s.id} ({s.class_room.name}) at scheduled end time {s.end_time}.")
                try:
                    send_absence_alerts_for_session.delay(s.id)
                except Exception as exc:
                    logger.warning(f"Could not queue async alert for session {s.id} via Celery: {exc}. Dispatching in background thread.")
                    import threading
                    threading.Thread(
                        target=send_absence_alerts_for_session,
                        args=(s.id,),
                        daemon=True,
                        name=f"alert-session-{s.id}"
                    ).start()
                continue

        # 2. Auto-Start check
        if s.ended_at is None and s.date == today:
            if s_start_dt <= now < s_end_dt:
                if not s.qr_token or not s.qr_token_expires_at or s.qr_token_expires_at < now:
                    s.qr_token = secrets.token_urlsafe(32)
                    s.qr_token_expires_at = s_end_dt
                    update_fields = ["qr_token", "qr_token_expires_at"]
                    if not s.started_at:
                        s.started_at = now
                        update_fields.append("started_at")
                    s.save(update_fields=update_fields)
                    started_count += 1
                    logger.info(f"Auto-started session {s.id} ({s.class_room.name}) with QR validity until {s.end_time}.")

    return {
        "auto_started": started_count,
        "auto_ended": ended_count,
    }


@shared_task
def auto_manage_session_lifecycle():
    """
    Celery periodic task to automatically start scheduled sessions and end completed sessions.
    """
    return sync_sessions_lifecycle()
