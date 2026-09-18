import logging
import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from .models import AlertLog, AttendanceRecord, Session

logger = logging.getLogger(__name__)


def send_telegram_alert(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, json=payload, timeout=10)
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

    # Get all active students enrolled in this session's class
    students = session.class_room.students.filter(is_active=True)
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")

    sent_count = 0
    failed_count = 0

    for student in students:
        # Check if attendance was already recorded for this session
        record, created = AttendanceRecord.objects.get_or_create(
            student=student,
            session=session,
            defaults={"status": "absent", "method": "manual"}
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
        if contact:
            if contact.startswith("@") or contact.lstrip("-").isdigit():
                # Telegram handle or numerical chat_id
                channel = "telegram"
                if not bot_token:
                    error_msg = "TELEGRAM_BOT_TOKEN is not configured in settings."
                else:
                    try:
                        send_telegram_alert(bot_token, contact, message_text)
                        success = True
                    except Exception as exc:
                        error_msg = str(exc)
                        logger.warning(f"Telegram send failed for {student.student_id}: {exc}")
            elif "@" in contact:
                # Email format
                channel = "email"
                try:
                    send_email_alert(contact, student.full_name, session.class_room.name, str(session.date))
                    success = True
                except Exception as exc:
                    error_msg = str(exc)
                    logger.warning(f"Email send failed for {student.student_id}: {exc}")
            else:
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
