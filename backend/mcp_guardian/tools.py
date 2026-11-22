"""Guardian MCP Tool Implementations.

Manages guardian authorization workflows, WhatsApp notifications (via Twilio),
cooling-off delays, emergency alerts, and safety rule queries.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional
import uuid

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    put_item,
    update_item,
    get_household_rules,
    get_household_members,
    get_approval,
    query_by_prefix,
)
from backend.common.models import SKPrefix
from backend.common.twilio_client import send_whatsapp_message

logger = logging.getLogger(__name__)


def _get_guardian_phone(household_id: str) -> str:
    """Retrieve guardian WhatsApp phone number from household records or settings."""
    pk = format_pk(household_id)
    # Check MEMBER#ARJUN or any guardian member
    for sk in ("MEMBER#ARJUN", "MEMBER#guardian"):
        member = get_item(pk, sk)
        if member and member.get("phone"):
            return member["phone"]

    members = get_household_members(pk)
    for m in members:
        if m.get("role") == "GUARDIAN" and m.get("phone"):
            return m["phone"]

    return settings.GUARDIAN_WHATSAPP_TO


def request_approval(
    household_id: Optional[str] = None,
    action_type: str = "PAYMENT",
    payee_name: str = "",
    amount: float = 0.0,
    reason: str = "",
    bill_id: Optional[str] = None,
    med_ids: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Initiate a Guardian approval request with WhatsApp notification.

    - Generates approval_id (APR_<8-char-hex>).
    - Reads cooling_off_seconds from RULE#CONFIG (fallback config.COOLING_OFF_SECONDS, default 20).
    - Saves APPROVAL#<approval_id> with:
        status="PENDING", created_at (epoch int), ttl = now + APPROVAL_TTL_SECONDS,
        cooling_off_seconds, approved_at=None, executable_at=None.
    - Sends WhatsApp message to guardian via send_whatsapp_message.
    - Logs AUDIT# and returns {approval_id, status: "PENDING", cooling_off_seconds, expires_in_seconds}.
    """
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    amount = float(amount)
    rules = get_household_rules(hh_id) or {}
    cooling_off_seconds = int(rules.get("cooling_off_seconds", settings.COOLING_OFF_SECONDS))
    ttl_seconds = int(settings.APPROVAL_TTL_SECONDS)

    approval_id = f"APR_{uuid.uuid4().hex[:8].upper()}"
    now_dt = datetime.now(timezone.utc)
    now_epoch = int(now_dt.timestamp())
    ttl_epoch = now_epoch + ttl_seconds
    now_iso = now_dt.isoformat()

    approval_item = {
        "PK": hh_id,
        "SK": f"{SKPrefix.APPROVAL}{approval_id}",
        "approval_id": approval_id,
        "household_id": hh_id,
        "status": "PENDING",
        "action_type": action_type,
        "payee_name": payee_name,
        "amount": amount,
        "reason": reason,
        "bill_id": bill_id,
        "med_ids": med_ids,
        "created_at": now_epoch,
        "requested_at": now_iso,
        "ttl": ttl_epoch,
        "cooling_off_seconds": cooling_off_seconds,
        "approved_at": None,
        "executable_at": None,
        "entity_type": "APPROVAL",
    }
    put_item(approval_item)

    # Format outbound WhatsApp message
    guardian_phone = _get_guardian_phone(hh_id)
    wa_body = (
        f"BETA Approval Needed: ₹{amount:,.2f} to {payee_name} ({reason}). "
        f"Reply 1 to APPROVE or 2 to DECLINE. Ref: {approval_id}"
    )
    send_whatsapp_message(to_number=guardian_phone, body=wa_body)

    log_audit(
        household_id=hh_id,
        action="GUARDIAN_APPROVAL_REQUESTED",
        actor="SYSTEM:GUARDIAN_MCP",
        details={
            "approval_id": approval_id,
            "action_type": action_type,
            "payee_name": payee_name,
            "amount": amount,
            "reason": reason,
            "cooling_off_seconds": cooling_off_seconds,
            "ttl": ttl_epoch,
        },
        risk_score=0.2,
        status="PENDING",
    )

    return {
        "success": True,
        "approval_id": approval_id,
        "status": "PENDING",
        "cooling_off_seconds": cooling_off_seconds,
        "expires_in_seconds": ttl_seconds,
        "amount": amount,
        "payee_name": payee_name,
    }


def get_approval_status(
    household_id: Optional[str] = None,
    approval_id: str = "",
) -> Dict[str, Any]:
    """Retrieve computed live status of an approval request.

    - Missing or now > ttl -> 'EXPIRED'
    - status == 'DECLINED' -> 'DECLINED'
    - status == 'CANCELLED' -> 'CANCELLED'
    - status == 'PENDING' -> 'PENDING'
    - status == 'APPROVED' and now < executable_at -> 'APPROVED_COOLING_OFF' with seconds_remaining
    - status == 'APPROVED' and now >= executable_at -> 'READY' with seconds_remaining = 0
    """
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    item = get_approval(hh_id, approval_id)
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    if not item:
        return {
            "approval_id": approval_id,
            "status": "EXPIRED",
            "seconds_remaining": 0,
            "amount": 0.0,
            "payee_name": "",
            "executable_at": None,
        }

    # Check TTL expiration
    ttl = item.get("ttl")
    if ttl and now_epoch > int(ttl):
        return {
            "approval_id": approval_id,
            "status": "EXPIRED",
            "seconds_remaining": 0,
            "amount": float(item.get("amount", 0.0)),
            "payee_name": item.get("payee_name", ""),
            "executable_at": item.get("executable_at"),
        }

    raw_status = str(item.get("status", "PENDING")).upper()
    executable_at = item.get("executable_at")

    if raw_status in ("DECLINED", "REJECTED"):
        status = "DECLINED"
        seconds_remaining = 0
    elif raw_status == "CANCELLED":
        status = "CANCELLED"
        seconds_remaining = 0
    elif raw_status == "PENDING":
        status = "PENDING"
        seconds_remaining = 0
    elif raw_status == "APPROVED":
        if executable_at is not None and now_epoch < int(executable_at):
            status = "APPROVED_COOLING_OFF"
            seconds_remaining = max(0, int(int(executable_at) - now_epoch))
        else:
            status = "READY"
            seconds_remaining = 0
    else:
        status = raw_status
        seconds_remaining = 0

    return {
        "approval_id": approval_id,
        "status": status,
        "seconds_remaining": seconds_remaining,
        "amount": float(item.get("amount", 0.0)),
        "payee_name": item.get("payee_name", ""),
        "executable_at": executable_at,
    }


def notify_guardian(
    household_id: Optional[str] = None,
    severity: str = "INFO",
    message: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Send an immediate WhatsApp priority notification to the guardian.

    Used for R2/R1 payment receipts and R4 scam alerts (including 1930 cyber helpline advice).
    """
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    now_iso = datetime.now(timezone.utc).isoformat()
    guardian_phone = _get_guardian_phone(hh_id)

    # Format message with urgency context if HIGH or CRITICAL
    formatted_body = message
    if severity.upper() in ("HIGH", "CRITICAL"):
        formatted_body = (
            f"🚨 *BETA SECURITY ALERT [{severity.upper()}]*\n\n"
            f"{message}\n\n"
            f"⚠️ If suspicious activity or digital arrest is suspected, contact the "
            f"National Cyber Crime Helpline at 1930 immediately or visit cybercrime.gov.in.\n"
            f"Reply FREEZE to immediately lock all household money transactions."
        )

    res = send_whatsapp_message(to_number=guardian_phone, body=formatted_body)

    log_audit(
        household_id=hh_id,
        action="GUARDIAN_NOTIFICATION_SENT",
        actor="SYSTEM:GUARDIAN_MCP",
        details={
            "severity": severity,
            "message": message,
            "sid": res.get("sid"),
        },
        risk_score=0.9 if severity.upper() in ("HIGH", "CRITICAL") else 0.1,
        status="SENT",
    )

    return {
        "success": True,
        "notified": True,
        "severity": severity,
        "message": message,
        "sid": res.get("sid"),
    }


def get_guard_status(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Return frozen status, limits, and active approvals in PENDING or APPROVED_COOLING_OFF state."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    rules = get_household_rules(hh_id) or {}

    # Query all approvals for the household
    raw_approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
    active_approvals = []
    for item in raw_approvals:
        app_id = item.get("approval_id") or item.get("SK", "").replace(SKPrefix.APPROVAL, "")
        live = get_approval_status(hh_id, app_id)
        if live["status"] in ("PENDING", "APPROVED_COOLING_OFF"):
            active_approvals.append(live)

    return {
        "success": True,
        "household_id": hh_id,
        "frozen": bool(rules.get("frozen", False)),
        "auto_limit": float(rules.get("auto_limit", 2000.0)),
        "max_with_approval": float(rules.get("max_with_approval", 25000.0)),
        "cooling_off_seconds": int(rules.get("cooling_off_seconds", 20)),
        "active_approvals": active_approvals,
        "active_approvals_count": len(active_approvals),
    }


# Backward compatibility aliases
def request_guardian_approval(
    errand_type: str = "EXPENSE",
    payee_name: str = "",
    amount: float = 0.0,
    reason: Optional[str] = None,
    household_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    return request_approval(
        household_id=household_id,
        action_type=errand_type,
        payee_name=payee_name,
        amount=amount,
        reason=reason or "",
    )


def check_approval_status(
    approval_id: str,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_approval_status(household_id, approval_id)


def send_guardian_alert(
    alert_type: str = "ALERT",
    message: str = "",
    severity: str = "HIGH",
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    return notify_guardian(household_id=household_id, severity=severity, message=message)


def get_household_rules_tool(household_id: Optional[str] = None) -> Dict[str, Any]:
    return get_guard_status(household_id)
