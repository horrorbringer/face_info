import ipaddress
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def get_client_ip(request) -> str:
    """
    Extracts the client IP address from the request, respecting proxy headers.
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first IP from the comma-separated list of proxies
        client_ip = x_forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.META.get("REMOTE_ADDR", "").strip()
    return client_ip


def is_client_ip_allowed(request) -> bool:
    """
    Validates if the client's IP falls within the allowed attendance subnets.
    If ATTENDANCE_ALLOWED_SUBNETS is empty or not configured, all IPs are permitted.
    """
    allowed_subnets = getattr(settings, "ATTENDANCE_ALLOWED_SUBNETS", [])
    if not allowed_subnets:
        return True

    client_ip_str = get_client_ip(request)
    if not client_ip_str:
        logger.warning("Could not determine client IP address.")
        return False

    try:
        client_ip = ipaddress.ip_address(client_ip_str)
    except ValueError:
        logger.warning(f"Invalid client IP format: {client_ip_str}")
        return False

    for subnet_str in allowed_subnets:
        try:
            # Supports single IP or CIDR network (e.g. 192.168.1.0/24 or 127.0.0.1)
            network = ipaddress.ip_network(subnet_str, strict=False)
            if client_ip in network:
                return True
        except ValueError:
            logger.error(f"Invalid subnet in ATTENDANCE_ALLOWED_SUBNETS: {subnet_str}")
            continue

    logger.warning(f"Attendance rejected: IP {client_ip_str} not in allowed subnets {allowed_subnets}")
    return False


import hashlib
import hmac
from django.utils import timezone


def get_dynamic_qr_window(interval_seconds: int = None) -> int:
    interval = interval_seconds or getattr(settings, "DYNAMIC_QR_INTERVAL_SECONDS", 20)
    return int(timezone.now().timestamp() // interval)


def generate_dynamic_qr_token(session_id: int, window_offset: int = 0) -> str:
    """
    Generates a secure, time-windowed dynamic QR token for a session.
    Changes every DYNAMIC_QR_INTERVAL_SECONDS (default 20s).
    """
    interval = getattr(settings, "DYNAMIC_QR_INTERVAL_SECONDS", 20)
    window = get_dynamic_qr_window(interval) + window_offset
    salt = getattr(settings, "ATTENDANCE_QR_SALT", "") or getattr(settings, "SECRET_KEY", "fallback-secret")
    secret = f"{salt}:qr_dyn_v1"
    payload = f"session:{session_id}:win:{window}".encode("utf-8")
    token_hash = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()[:24]
    return f"dyn_{session_id}_{token_hash}"


def verify_dynamic_qr_token(session_id: int, token_str: str) -> bool:
    """
    Verifies a dynamic QR token against the current time window and the
    immediate preceding window (sliding tolerance for transmission latency).
    """
    if not token_str.startswith(f"dyn_{session_id}_"):
        return False

    current_token = generate_dynamic_qr_token(session_id, window_offset=0)
    if hmac.compare_digest(token_str, current_token):
        return True

    prev_token = generate_dynamic_qr_token(session_id, window_offset=-1)
    if hmac.compare_digest(token_str, prev_token):
        return True

    return False


def get_dynamic_qr_info(session_id: int) -> dict:
    """
    Returns the active dynamic QR token along with expiry countdown seconds.
    """
    interval = getattr(settings, "DYNAMIC_QR_INTERVAL_SECONDS", 20)
    now_ts = int(timezone.now().timestamp())
    seconds_remaining = interval - (now_ts % interval)
    if seconds_remaining == 0:
        seconds_remaining = interval

    return {
        "session_id": session_id,
        "token": generate_dynamic_qr_token(session_id, window_offset=0),
        "interval_seconds": interval,
        "expires_in_seconds": seconds_remaining,
        "server_timestamp": now_ts,
    }


from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication, get_authorization_header


class FlexibleTokenAuthentication(TokenAuthentication):
    """
    Tolerant Token Authentication:
    Accepts any of the following in the HTTP 'Authorization' header:
      1. Authorization: Token <key>     (standard DRF)
      2. Authorization: Bearer <key>    (OAuth standard)
      3. Authorization: <key>           (raw 40-character token pasted into Swagger UI or curl)
    """
    keyword = "Token"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()

        if not auth:
            return None

        # Case 1: Standard two-part header ("Token <key>" or "Bearer <key>")
        if len(auth) == 2:
            prefix = auth[0].decode("utf-8", errors="ignore").lower()
            if prefix in ("token", "bearer"):
                try:
                    token_key = auth[1].decode("utf-8")
                except UnicodeError:
                    raise exceptions.AuthenticationFailed("Invalid token header format.")
                return self.authenticate_credentials(token_key)

        # Case 2: Single-part header ("<key>" directly without prefix)
        if len(auth) == 1:
            try:
                token_key = auth[0].decode("utf-8")
            except UnicodeError:
                raise exceptions.AuthenticationFailed("Invalid token header format.")
            if len(token_key) == 40:
                return self.authenticate_credentials(token_key)

        return super().authenticate(request)


