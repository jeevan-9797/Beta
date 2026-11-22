"""Twilio WhatsApp Webhook Handler for Guardian interactions.

Parses incoming WhatsApp replies from Arjun (e.g. 'APPROVE APP_123', 'REJECT APP_123',
'FREEZE', 'UNFREEZE', 'STATUS') and updates DynamoDB state accordingly.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, Tuple

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    update_item,
    query_by_prefix,
    get_household_rules,
)
from backend.common.models import ApprovalStatus, SKPrefix

logger = logging.getLogger(__name__)


def process_whatsapp_message(
    from_number: str,
    body: str,
    household_id: str = settings.DEFAULT_HOUSEHOLD_ID,
) -> str:
    """Process incoming WhatsApp message text from guardian and return reply text."""
    clean_body = body.strip().upper()
    hh_id = format_pk(household_id)

    logger.info(f"Incoming WhatsApp message from {from_number}: '{clean_body}'")

    # Command: EMERGENCY FREEZE
    if clean_body in ("FREEZE", "EMERGENCY FREEZE", "LOCK", "STOP"):
        update_item(
            hh_id,
            f"{SKPrefix.RULE}FINANCIAL",
            {"frozen": True, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        log_audit(
            household_id=hh_id,
            action="EMERGENCY_FREEZE_TRIGGERED",
            actor=f"GUARDIAN:{from_number}",
            details={"trigger": clean_body},
            risk_score=1.0,
            status="SUCCESS",
        )
        return (
            "🛑 *EMERGENCY FREEZE ACTIVATED*\n\n"
            f"All financial errands and payments for household {hh_id} have been immediately locked. "
            "Kamala will be informed that transactions are paused. Reply *UNFREEZE* to restore normal operations."
        )

    # Command: UNFREEZE
    if clean_body in ("UNFREEZE", "UNLOCK", "RESUME"):
        update_item(
            hh_id,
            f"{SKPrefix.RULE}FINANCIAL",
            {"frozen": False, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        log_audit(
            household_id=hh_id,
            action="EMERGENCY_FREEZE_LIFTED",
            actor=f"GUARDIAN:{from_number}",
            details={"trigger": clean_body},
            risk_score=0.2,
            status="SUCCESS",
        )
        return (
            "✅ *EMERGENCY FREEZE LIFTED*\n\n"
            f"Household {hh_id} transactions are now active under normal guardrails."
        )

    # Command: STATUS
    if clean_body in ("STATUS", "CHECK", "PENDING"):
        approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
        pending = [
            a for a in approvals
            if a.get("status") in (ApprovalStatus.PENDING.value, ApprovalStatus.COOLING_OFF.value)
        ]
        rules = get_household_rules(hh_id) or {}
        frozen_state = "🛑 FROZEN" if rules.get("frozen") else "🟢 ACTIVE"

        if not pending:
            return f"ℹ️ *BETA Status for {hh_id}*\nStatus: {frozen_state}\nNo pending approval requests."

        lines = [f"📋 *Pending Approvals ({len(pending)})* | System: {frozen_state}\n"]
        for p in pending:
            lines.append(
                f"• ID: *{p.get('approval_id')}* | {p.get('payee_name')} | ₹{p.get('amount')} ({p.get('status')})"
            )
        lines.append("\nReply *APPROVE <ID>* or *REJECT <ID>*.")
        return "\n".join(lines)

    # Command: APPROVE or REJECT with ID
    match = re.match(r"^(APPROVE|REJECT|YES|NO)\s*([A-Za-z0-9_]*)$", clean_body)
    if match:
        action_verb = match.group(1)
        given_id = match.group(2).strip()

        # If guardian just typed APPROVE or YES without ID, find latest pending request
        if not given_id:
            approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
            pending = [
                a for a in approvals
                if a.get("status") in (ApprovalStatus.PENDING.value, ApprovalStatus.COOLING_OFF.value)
            ]
            if not pending:
                return "❓ No pending approval requests found to authorize."
            # Sort newest first
            pending.sort(key=lambda x: str(x.get("requested_at", "")), reverse=True)
            target_approval = pending[0]
            approval_id = target_approval.get("approval_id")
        else:
            approval_id = (
                given_id if given_id.startswith("APP_") else f"APP_{given_id}"
            )
            target_approval = get_item(hh_id, f"{SKPrefix.APPROVAL}{approval_id}")

        if not target_approval:
            return f"❌ Approval request '{approval_id}' not found."

        is_approve = action_verb in ("APPROVE", "YES")
        new_status = (
            ApprovalStatus.APPROVED.value
            if is_approve
            else ApprovalStatus.REJECTED.value
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        # Check cooling-off timer
        cooling_off_until = target_approval.get("cooling_off_until")
        if is_approve and cooling_off_until and now_iso < cooling_off_until:
            return (
                f"⏳ *Cooling-off Period Active*\n\n"
                f"Approval for {target_approval.get('payee_name')} (₹{target_approval.get('amount')}) "
                f"is in cooling-off delay to prevent hasty approvals.\n"
                f"Please reply APPROVE again after {cooling_off_until[11:19]} UTC."
            )

        update_item(
            hh_id,
            f"{SKPrefix.APPROVAL}{approval_id}",
            {
                "status": new_status,
                "guardian_response": action_verb,
                "responded_at": now_iso,
            },
        )

        log_audit(
            household_id=hh_id,
            action=f"GUARDIAN_APPROVAL_{new_status}",
            actor=f"GUARDIAN:{from_number}",
            details={
                "approval_id": approval_id,
                "amount": target_approval.get("amount"),
                "payee": target_approval.get("payee_name"),
            },
            risk_score=0.1 if is_approve else 0.5,
            status=new_status,
        )

        emoji = "✅" if is_approve else "🚫"
        return (
            f"{emoji} *Approval Updated*\n\n"
            f"Request *{approval_id}* ({target_approval.get('payee_name')}, ₹{target_approval.get('amount')}) "
            f"has been marked as *{new_status}*. BETA will proceed with the errand accordingly."
        )

    return (
        "❓ Unrecognized command. Available actions:\n"
        "• *STATUS* - View system status and pending requests\n"
        "• *APPROVE <ID>* - Authorize transaction\n"
        "• *REJECT <ID>* - Decline transaction\n"
        "• *FREEZE* - Emergency freeze all payments"
    )
