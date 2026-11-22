"""Guardian MCP Tool Implementations.

Manages guardian approval workflows, WhatsApp notifications (via Twilio),
cooling-off delays, emergency alerts, and safety rule queries.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, Optional
import uuid

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    put_item,
    update_item,
    get_household_rules,
)
from backend.common.models import ApprovalRequest, ApprovalStatus, SKPrefix

logger = logging.getLogger(__name__)

# Twilio client helper
try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None


def _send_whatsapp_message(to_number: str, body: str) -> bool:
    """Send WhatsApp message using Twilio API or mock fallback."""
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.info(
            f"[MOCK WHATSAPP DISPATCH] To: {to_number or settings.GUARDIAN_WHATSAPP_TO}\nBody: {body}"
        )
        return True

    if TwilioClient is None:
        logger.warning("twilio package not installed, logging mock message.")
        return True

    try:
        client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        recipient = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        message = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            body=body,
            to=recipient,
        )
        logger.info(f"Twilio WhatsApp message sent: SID {message.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch WhatsApp message via Twilio: {e}")
        return False


def request_guardian_approval(
    errand_type: str,
    payee_name: str,
    amount: float,
    reason: Optional[str] = None,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Initiate a Guardian approval request with configurable cooling-off timer."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    rules = get_household_rules(hh_id) or {}
    cooling_off_sec = int(rules.get("cooling_off_seconds", settings.COOLING_OFF_SECONDS))
    approval_ttl_sec = settings.APPROVAL_TTL_SECONDS

    approval_id = f"APP_{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc)
    requested_at = now.isoformat()
    cooling_off_until = (now + timedelta(seconds=cooling_off_sec)).isoformat()
    expires_at = (now + timedelta(seconds=approval_ttl_sec)).isoformat()
    ttl_epoch = int((now + timedelta(seconds=approval_ttl_sec)).timestamp())

    # Set initial status to COOLING_OFF if cooling_off_sec > 0 else PENDING
    initial_status = (
        ApprovalStatus.COOLING_OFF.value
        if cooling_off_sec > 0
        else ApprovalStatus.PENDING.value
    )

    approval_req = ApprovalRequest(
        approval_id=approval_id,
        household_id=hh_id,
        errand_type=errand_type,
        payee_name=payee_name,
        amount=amount,
        status=initial_status,
        requested_at=requested_at,
        cooling_off_until=cooling_off_until,
        expires_at=expires_at,
        ttl=ttl_epoch,
    )
    put_item(approval_req.to_item())

    # Format WhatsApp notification for Arjun (Guardian)
    reason_str = f"\n*Reason:* {reason}" if reason else ""
    whatsapp_body = (
        f"🚨 *BETA Guardian Approval Request*\n\n"
        f"Kamala has requested an errand that requires your authorization:\n"
        f"• *Item/Errand:* {errand_type}\n"
        f"• *Payee:* {payee_name}\n"
        f"• *Amount:* ₹{amount:,.2f}{reason_str}\n"
        f"• *Cooling-off delay:* {cooling_off_sec} seconds\n\n"
        f"Reply *APPROVE {approval_id}* or *REJECT {approval_id}*."
    )

    _send_whatsapp_message(settings.GUARDIAN_WHATSAPP_TO, whatsapp_body)

    log_audit(
        household_id=hh_id,
        action="APPROVAL_REQUESTED",
        actor="SYSTEM:GUARDIAN_MCP",
        details={
            "approval_id": approval_id,
            "errand_type": errand_type,
            "payee_name": payee_name,
            "amount": amount,
            "cooling_off_sec": cooling_off_sec,
        },
        risk_score=0.3,
        status="PENDING_APPROVAL",
    )

    return {
        "success": True,
        "approval_id": approval_id,
        "status": initial_status,
        "amount": amount,
        "payee_name": payee_name,
        "cooling_off_seconds": cooling_off_sec,
        "cooling_off_until": cooling_off_until,
        "message": (
            f"Sent approval request to Arjun on WhatsApp for ₹{amount} to {payee_name}. "
            f"Cooling-off delay of {cooling_off_sec} seconds is active."
        ),
    }


def check_approval_status(
    approval_id: str,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Check current status of an approval request, advancing cooling-off state if elapsed."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    sk = (
        approval_id
        if approval_id.startswith(SKPrefix.APPROVAL)
        else f"{SKPrefix.APPROVAL}{approval_id}"
    )
    item = get_item(hh_id, sk)

    if not item:
        return {
            "success": False,
            "error": f"Approval request '{approval_id}' not found.",
        }

    current_status = item.get("status")
    now_iso = datetime.now(timezone.utc).isoformat()

    # Check for expiration
    expires_at = item.get("expires_at")
    if expires_at and now_iso > expires_at and current_status not in (
        ApprovalStatus.APPROVED.value,
        ApprovalStatus.REJECTED.value,
    ):
        update_item(hh_id, sk, {"status": ApprovalStatus.EXPIRED.value})
        current_status = ApprovalStatus.EXPIRED.value

    # Check if cooling-off period has passed
    cooling_off_until = item.get("cooling_off_until")
    if (
        current_status == ApprovalStatus.COOLING_OFF.value
        and cooling_off_until
        and now_iso >= cooling_off_until
    ):
        update_item(hh_id, sk, {"status": ApprovalStatus.PENDING.value})
        current_status = ApprovalStatus.PENDING.value

    return {
        "success": True,
        "approval_id": item.get("approval_id"),
        "status": current_status,
        "amount": item.get("amount"),
        "payee_name": item.get("payee_name"),
        "guardian_response": item.get("guardian_response"),
    }


def send_guardian_alert(
    alert_type: str,
    message: str,
    severity: str = "HIGH",
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an immediate WhatsApp priority alert to the guardian."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)

    whatsapp_body = (
        f"⚠️ *BETA SECURITY ALERT [{severity}]*\n\n"
        f"Household: {hh_id}\n"
        f"Incident: {alert_type}\n"
        f"Details: {message}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%d %b %Y, %I:%M %p UTC')}\n\n"
        f"If this was not authorized, reply *FREEZE* immediately to freeze all transactions."
    )

    sent = _send_whatsapp_message(settings.GUARDIAN_WHATSAPP_TO, whatsapp_body)

    log_audit(
        household_id=hh_id,
        action=f"ALERT:{alert_type}",
        actor="SYSTEM:GUARDIAN_MCP",
        details={"severity": severity, "message": message, "dispatched": sent},
        risk_score=0.9 if severity in ("HIGH", "CRITICAL") else 0.5,
        status="ALERTED",
    )

    return {
        "success": True,
        "alert_type": alert_type,
        "severity": severity,
        "dispatched": sent,
        "message": f"Alert sent to Guardian WhatsApp with severity {severity}.",
    }


def get_household_rules_tool(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch active safety rules and spending limits."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    rules = get_household_rules(hh_id)

    if not rules:
        return {
            "success": False,
            "error": f"No rules configured for household {hh_id}.",
        }

    return {
        "success": True,
        "household_id": hh_id,
        "auto_limit": rules.get("auto_limit", 2000.0),
        "max_with_approval": rules.get("max_with_approval", 25000.0),
        "frozen": rules.get("frozen", False),
        "cooling_off_seconds": rules.get("cooling_off_seconds", 20),
        "night_curfew": f"{rules.get('night_start', 23)}:00 - {rules.get('night_end', 6)}:00 IST",
        "max_payments_per_hour": rules.get("max_payments_per_hour", 2),
    }
