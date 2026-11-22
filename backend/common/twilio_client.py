"""Twilio WhatsApp client helper for BETA.

Handles outbound WhatsApp notifications for guardian approvals and alerts.
Supports auto-prefixing 'whatsapp:', credential validation, and offline mock fallback.
"""

import logging
from typing import Any, Dict
import uuid

from backend.common.config import settings

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client as TwilioClient

    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    TwilioClient = None


def format_whatsapp_number(number: str) -> str:
    """Ensure phone number has 'whatsapp:' prefix."""
    clean = (number or "").strip()
    if not clean:
        return ""
    if clean.startswith("whatsapp:"):
        return clean
    return f"whatsapp:{clean}"


def send_whatsapp_message(to_number: str, body: str) -> Dict[str, Any]:
    """Send an outbound WhatsApp message using Twilio API or offline mock fallback.

    Args:
        to_number: Destination WhatsApp number (e.g. '+919876543210' or 'whatsapp:+919876543210')
        body: Text content of the WhatsApp message.

    Returns:
        dict: {"sid": str, "mock": bool, "to": str, "body": str}
    """
    recipient = format_whatsapp_number(to_number or settings.GUARDIAN_WHATSAPP_TO)
    sender = format_whatsapp_number(settings.TWILIO_WHATSAPP_FROM or "whatsapp:+14155238886")

    has_credentials = bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)
    use_mock = settings.LOCAL_MOCK_DB or not has_credentials or not TWILIO_AVAILABLE

    if use_mock:
        mock_sid = f"MOCK_WA_{uuid.uuid4().hex[:10].upper()}"
        logger.info(
            f"[MOCK WHATSAPP DISPATCH] To: {recipient} | From: {sender} | SID: {mock_sid}\n"
            f"Body:\n{body}"
        )
        return {
            "sid": mock_sid,
            "mock": True,
            "to": recipient,
            "body": body,
        }

    try:
        client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=sender,
            body=body,
            to=recipient,
        )
        logger.info(f"Twilio WhatsApp message sent: SID {message.sid} to {recipient}")
        return {
            "sid": message.sid,
            "mock": False,
            "to": recipient,
            "body": body,
        }
    except Exception as e:
        logger.error(f"Failed to dispatch Twilio WhatsApp message to {recipient}: {e}")
        # Graceful fallback on error so application flows are not halted
        mock_sid = f"ERR_FALLBACK_{uuid.uuid4().hex[:8].upper()}"
        return {
            "sid": mock_sid,
            "mock": True,
            "error": str(e),
            "to": recipient,
            "body": body,
        }
