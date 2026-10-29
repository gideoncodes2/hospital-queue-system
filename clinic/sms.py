"""SMS sending. A failed SMS must never break booking or the queue, so nothing here raises."""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

AT_SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
AT_LIVE_URL = "https://api.africastalking.com/version1/messaging"


def send_sms(phone, message):
    """Send one SMS. Returns True if it was handed off successfully."""
    if settings.SMS_BACKEND == "africastalking":
        return _send_africastalking(phone, message)
    logger.info("[SMS to %s] %s", phone, message)
    return True


def _send_africastalking(phone, message):
    url = AT_SANDBOX_URL if settings.AT_SANDBOX else AT_LIVE_URL
    data = {"username": settings.AT_USERNAME, "to": phone, "message": message}
    if settings.AT_SENDER_ID:
        data["from"] = settings.AT_SENDER_ID
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={
            "apiKey": settings.AT_API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("Africa's Talking request failed: %s", exc)
        return False
    recipients = body.get("SMSMessageData", {}).get("Recipients", [])
    ok = bool(recipients) and all(r.get("statusCode") in (100, 101, 102) for r in recipients)
    if not ok:
        logger.warning("Africa's Talking rejected the message: %s", body)
    return ok
