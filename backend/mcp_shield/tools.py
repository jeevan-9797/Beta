"""Shield MCP Tool Implementations.

Scam detection, payee trust verification, cooling-off countdown calculation,
and emergency freeze management.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    update_item,
    get_household_rules,
    get_payees,
    get_billers,
)
from backend.common.models import SKPrefix
from backend.interceptors.request_interceptor import detect_scam_keywords

logger = logging.getLogger(__name__)


def screen_scam_risk(
    text: str,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze text for Indian senior citizen fraud patterns."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    is_scam = detect_scam_keywords(text)
    risk_score = 0.95 if is_scam else 0.05

    if is_scam:
        log_audit(
            household_id=hh_id,
            action="SCAM_SCREENED_POSITIVE",
            actor="MCP:SHIELD",
            details={"flagged_text": text, "risk_score": risk_score},
            risk_score=risk_score,
            status="ALERT",
        )

    return {
        "success": True,
        "scam_detected": is_scam,
        "risk_score": risk_score,
        "recommendation": (
            "BLOCK_AND_ALERT_GUARDIAN"
            if is_scam
            else "PROCEED_WITH_STANDARD_GUARDRAILS"
        ),
    }


def verify_payee_trust(
    payee_name: str,
    upi_id: Optional[str] = None,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Check if the payee is saved and trusted for the household."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    target = payee_name.strip().upper()

    # Search billers
    for b in get_billers(hh_id):
        if (
            b.get("biller_id", "").upper() == target
            or b.get("name", "").upper() == target
        ):
            return {
                "success": True,
                "is_known": True,
                "category": "UTILITY",
                "payee_name": b.get("name"),
                "status": "APPROVED_BILLER",
            }

    # Search payees
    for p in get_payees(hh_id):
        if (
            p.get("payee_id", "").upper() == target
            or p.get("name", "").upper() == target
            or target in p.get("name", "").upper()
        ):
            return {
                "success": True,
                "is_known": True,
                "category": p.get("category", "MERCHANT"),
                "payee_name": p.get("name"),
                "status": "APPROVED_PAYEE",
            }

    return {
        "success": True,
        "is_known": False,
        "category": "UNKNOWN",
        "payee_name": payee_name,
        "status": "UNAUTHORIZED_PAYEE",
        "warning": f"Payee '{payee_name}' is not registered on the whitelist for {hh_id}.",
    }


def evaluate_cooling_off(
    approval_id: str,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Check cooling-off status and calculate remaining duration in seconds."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    sk = (
        approval_id
        if approval_id.startswith(SKPrefix.APPROVAL)
        else f"{SKPrefix.APPROVAL}{approval_id}"
    )
    item = get_item(hh_id, sk)

    if not item:
        return {"success": False, "error": f"Approval {approval_id} not found."}

    cooling_off_until_str = item.get("cooling_off_until")
    if not cooling_off_until_str:
        return {
            "success": True,
            "in_cooling_off": False,
            "seconds_remaining": 0,
        }

    now = datetime.now(timezone.utc)
    try:
        until_dt = datetime.fromisoformat(cooling_off_until_str)
        remaining = int((until_dt - now).total_seconds())
    except Exception:
        remaining = 0

    in_cooling_off = remaining > 0

    return {
        "success": True,
        "approval_id": approval_id,
        "in_cooling_off": in_cooling_off,
        "seconds_remaining": max(0, remaining),
        "cooling_off_until": cooling_off_until_str,
    }


def trigger_emergency_freeze(
    reason: str,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Emergency lockout of all transactions."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    now_iso = datetime.now(timezone.utc).isoformat()

    update_item(
        hh_id,
        f"{SKPrefix.RULE}FINANCIAL",
        {"frozen": True, "updated_at": now_iso, "freeze_reason": reason},
    )

    log_audit(
        household_id=hh_id,
        action="EMERGENCY_FREEZE_ACTIVATED",
        actor="MCP:SHIELD",
        details={"reason": reason},
        risk_score=1.0,
        status="LOCKED",
    )

    return {
        "success": True,
        "household_id": hh_id,
        "frozen": True,
        "message": f"Emergency freeze successfully engaged for {hh_id}: {reason}",
    }
