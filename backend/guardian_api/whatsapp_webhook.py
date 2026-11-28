"""Twilio WhatsApp Webhook Handler for Guardian interactions.

Parses incoming WhatsApp replies from Arjun (e.g. '1', 'APPROVE', '2', 'DECLINE',
'CANCEL', 'FREEZE', 'UNFREEZE', 'STATUS') and updates DynamoDB state accordingly.
Provides lambda_handler returning TwiML XML.
"""

import base64
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional
import urllib.parse
from xml.sax.saxutils import escape

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    update_item,
    query_by_prefix,
    get_household_rules,
)
from backend.common.models import SKPrefix
from backend.common.twilio_client import format_whatsapp_number

logger = logging.getLogger(__name__)


def format_twiml_response(message: str) -> str:
    """Wrap message text in Twilio TwiML XML response."""
    safe_msg = escape(message)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"    <Message>{safe_msg}</Message>\n"
        "</Response>"
    )


def _get_household_for_phone(from_number: str, default_hh: str) -> str:
    """Map sender phone number to household ID if known, else default."""
    # In future multi-tenant, query GSI by phone. For now, use default_hh.
    return format_pk(default_hh)


def process_whatsapp_message(
    from_number: str,
    body: str,
    household_id: str = settings.DEFAULT_HOUSEHOLD_ID,
) -> str:
    """Process incoming WhatsApp message text from guardian and return reply text."""
    clean_body = body.strip()
    upper_body = clean_body.upper()
    hh_id = _get_household_for_phone(from_number, household_id)
    now_dt = datetime.now(timezone.utc)
    now_epoch = int(now_dt.timestamp())
    now_iso = now_dt.isoformat()

    logger.info(f"Incoming WhatsApp message from {from_number} for {hh_id}: '{clean_body}'")

    # Command: EMERGENCY FREEZE
    if upper_body in ("FREEZE", "EMERGENCY FREEZE", "LOCK", "STOP"):
        update_item(
            hh_id,
            f"{SKPrefix.RULE}CONFIG",
            {"frozen": True, "updated_at": now_iso},
        )
        update_item(
            hh_id,
            f"{SKPrefix.RULE}FINANCIAL",
            {"frozen": True, "updated_at": now_iso},
        )
        log_audit(
            household_id=hh_id,
            action="EMERGENCY_FREEZE_TRIGGERED",
            actor=f"GUARDIAN:{from_number}",
            details={"trigger": upper_body},
            risk_score=1.0,
            status="SUCCESS",
        )
        return (
            "🛑 *EMERGENCY FREEZE ACTIVATED*\n\n"
            f"All financial errands and payments for household {hh_id} have been immediately locked. "
            "Kamala will be informed that transactions are paused. Reply *UNFREEZE* to restore normal operations."
        )

    # Command: UNFREEZE
    if upper_body in ("UNFREEZE", "UNLOCK", "RESUME"):
        update_item(
            hh_id,
            f"{SKPrefix.RULE}CONFIG",
            {"frozen": False, "updated_at": now_iso},
        )
        update_item(
            hh_id,
            f"{SKPrefix.RULE}FINANCIAL",
            {"frozen": False, "updated_at": now_iso},
        )
        log_audit(
            household_id=hh_id,
            action="EMERGENCY_FREEZE_LIFTED",
            actor=f"GUARDIAN:{from_number}",
            details={"trigger": upper_body},
            risk_score=0.2,
            status="SUCCESS",
        )
        return (
            "✅ *EMERGENCY FREEZE LIFTED*\n\n"
            f"Household {hh_id} transactions are now active under normal guardrails."
        )

    # Command: STATUS
    if upper_body in ("STATUS", "CHECK", "PENDING"):
        approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
        active = []
        for a in approvals:
            status = a.get("status")
            ttl = a.get("ttl")
            if ttl and now_epoch > int(ttl):
                continue
            if status in ("PENDING", "APPROVED"):
                active.append(a)

        rules = get_household_rules(hh_id) or {}
        frozen_state = "🛑 FROZEN" if rules.get("frozen") else "🟢 ACTIVE"

        if not active:
            return f"ℹ️ *BETA Status for {hh_id}*\nStatus: {frozen_state}\nNo active approval requests."

        lines = [f"📋 *Active Approvals ({len(active)})* | System: {frozen_state}\n"]
        for p in active:
            app_id = p.get("approval_id") or p.get("SK", "").replace(SKPrefix.APPROVAL, "")
            st = p.get("status")
            exec_at = p.get("executable_at")
            if st == "APPROVED" and exec_at and now_epoch < int(exec_at):
                st = f"COOLING_OFF ({int(exec_at) - now_epoch}s left)"
            lines.append(
                f"• ID: *{app_id}* | {p.get('payee_name')} | ₹{p.get('amount')} ({st})"
            )
        lines.append("\nReply *1* to APPROVE, *2* to DECLINE, or *CANCEL*.")
        return "\n".join(lines)

    # Command: CANCEL (during cooling-off period)
    cancel_match = re.match(r"^CANCEL(?:\s+([A-Za-z0-9_]+))?$", upper_body)
    if cancel_match:
        target_id = cancel_match.group(1)
        approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
        target_approval = None

        if target_id:
            norm_id = target_id if target_id.startswith("APR_") else f"APR_{target_id}"
            for a in approvals:
                app_id = a.get("approval_id") or a.get("SK", "").replace(SKPrefix.APPROVAL, "")
                if app_id == norm_id:
                    target_approval = a
                    break
        else:
            # Find newest APPROVED approval within cooling-off
            cooling_apps = [
                a for a in approvals
                if a.get("status") == "APPROVED"
                and a.get("executable_at")
                and now_epoch < int(a.get("executable_at"))
            ]
            cooling_apps.sort(key=lambda x: int(x.get("approved_at") or x.get("created_at") or 0), reverse=True)
            if cooling_apps:
                target_approval = cooling_apps[0]

        if not target_approval:
            return "❓ No active cooling-off approvals found to cancel."

        app_id = target_approval.get("approval_id") or target_approval.get("SK", "").replace(SKPrefix.APPROVAL, "")
        status = target_approval.get("status")
        if status not in {"APPROVED", "PENDING"}:
            return f"Approval request {app_id} is {status}."
        updated = update_item(
            hh_id,
            f"{SKPrefix.APPROVAL}{app_id}",
            {
                "status": "CANCELLED",
                "cancelled_at": now_epoch,
                "guardian_response": "CANCELLED",
                "updated_at": now_iso,
            },
            expected_status=status,
        )
        if not updated:
            return f"Approval request {app_id} changed. Reply STATUS to refresh."
        log_audit(
            household_id=hh_id,
            action="GUARDIAN_APPROVAL_CANCELLED",
            actor=f"GUARDIAN:{from_number}",
            details={
                "approval_id": app_id,
                "amount": target_approval.get("amount"),
                "payee": target_approval.get("payee_name"),
            },
            risk_score=0.3,
            status="CANCELLED",
        )
        return (
            f"🛑 *Approval Cancelled*\n\n"
            f"Request *{app_id}* (₹{target_approval.get('amount')}) for {target_approval.get('payee_name')} "
            f"has been CANCELLED during cooling-off. The transaction will not proceed."
        )

    # Command: APPROVE / 1 / YES or DECLINE / 2 / NO
    # Matches: '1', 'APPROVE', 'YES', '2', 'DECLINE', 'REJECT', 'NO', with optional ID
    action_match = re.match(r"^(1|2|APPROVE|DECLINE|REJECT|YES|NO)(?:\s+([A-Za-z0-9_]+))?$", upper_body)
    if action_match:
        verb = action_match.group(1)
        given_id = action_match.group(2)
        is_approve = verb in ("1", "APPROVE", "YES")

        approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
        target_approval = None

        if given_id:
            norm_id = given_id if given_id.startswith("APR_") else f"APR_{given_id}"
            for a in approvals:
                app_id = a.get("approval_id") or a.get("SK", "").replace(SKPrefix.APPROVAL, "")
                if app_id == norm_id:
                    target_approval = a
                    break
        else:
            # Find newest PENDING approval
            pending = [a for a in approvals if a.get("status") == "PENDING"]
            # Exclude expired
            pending = [a for a in pending if not (a.get("ttl") and now_epoch > int(a.get("ttl")))]
            pending.sort(key=lambda x: int(x.get("created_at") or 0), reverse=True)
            if pending:
                target_approval = pending[0]

        if not target_approval:
            action_desc = "authorize" if is_approve else "decline"
            return f"❓ No pending approval requests found to {action_desc}."

        app_id = target_approval.get("approval_id") or target_approval.get("SK", "").replace(SKPrefix.APPROVAL, "")

        # Check TTL
        ttl = target_approval.get("ttl")
        if ttl and now_epoch > int(ttl):
            return f"❌ Approval request *{app_id}* has expired."

        if is_approve:
            rules = get_household_rules(hh_id) or {}
            cooling_off_seconds = int(
                target_approval.get("cooling_off_seconds")
                or rules.get("cooling_off_seconds", settings.COOLING_OFF_SECONDS)
            )
            executable_at = now_epoch + cooling_off_seconds
            updated = update_item(
                hh_id,
                f"{SKPrefix.APPROVAL}{app_id}",
                {
                    "status": "APPROVED",
                    "approved_at": now_epoch,
                    "executable_at": executable_at,
                    "cooling_off_seconds": cooling_off_seconds,
                    "guardian_response": "APPROVED",
                    "responded_at": now_iso,
                    "updated_at": now_iso,
                },
                expected_status="PENDING",
            )
            if not updated:
                return f"Approval request {app_id} is no longer pending. Reply STATUS to refresh."
            log_audit(
                household_id=hh_id,
                action="GUARDIAN_APPROVAL_APPROVED",
                actor=f"GUARDIAN:{from_number}",
                details={
                    "approval_id": app_id,
                    "amount": target_approval.get("amount"),
                    "payee": target_approval.get("payee_name"),
                    "cooling_off_seconds": cooling_off_seconds,
                    "executable_at": executable_at,
                },
                risk_score=0.1,
                status="APPROVED",
            )
            amount_val = float(target_approval.get("amount", 0))
            payee_val = target_approval.get("payee_name", "")
            if cooling_off_seconds > 0:
                return (
                    f"✅ *Approval Confirmed*\n\n"
                    f"Request *{app_id}* (₹{amount_val:,.2f} to {payee_val}) is approved.\n"
                    f"⏳ Cooling-off period active ({cooling_off_seconds}s). Ready for execution in {cooling_off_seconds}s.\n"
                    f"Reply *CANCEL* within {cooling_off_seconds}s to revoke."
                )
            else:
                return (
                    f"✅ *Approval Confirmed*\n\n"
                    f"Request *{app_id}* (₹{amount_val:,.2f} to {payee_val}) is approved and ready for immediate execution."
                )
        else:
            updated = update_item(
                hh_id,
                f"{SKPrefix.APPROVAL}{app_id}",
                {
                    "status": "DECLINED",
                    "guardian_response": "DECLINED",
                    "responded_at": now_iso,
                    "updated_at": now_iso,
                },
                expected_status="PENDING",
            )
            if not updated:
                return f"Approval request {app_id} is no longer pending. Reply STATUS to refresh."
            log_audit(
                household_id=hh_id,
                action="GUARDIAN_APPROVAL_DECLINED",
                actor=f"GUARDIAN:{from_number}",
                details={
                    "approval_id": app_id,
                    "amount": target_approval.get("amount"),
                    "payee": target_approval.get("payee_name"),
                },
                risk_score=0.4,
                status="DECLINED",
            )
            amount_val = float(target_approval.get("amount", 0))
            payee_val = target_approval.get("payee_name", "")
            return (
                f"🚫 *Request Declined*\n\n"
                f"Request *{app_id}* (₹{amount_val:,.2f} to {payee_val}) has been declined. BETA will not execute it."
            )

    return (
        "❓ Unrecognized command. Available actions:\n"
        "• *1* or *APPROVE* - Authorize pending request\n"
        "• *2* or *DECLINE* - Decline pending request\n"
        "• *CANCEL* - Abort approval during cooling-off period\n"
        "• *STATUS* - View active requests and security status\n"
        "• *FREEZE* - Emergency freeze all payments\n"
        "• *UNFREEZE* - Lift emergency freeze"
    )


def process_whatsapp_webhook(
    form_data: Dict[str, Any],
    household_id: str = settings.DEFAULT_HOUSEHOLD_ID,
) -> str:
    """Handle parsed URL-encoded form data from Twilio webhook."""
    from_number = form_data.get("From", "")
    body_text = form_data.get("Body", "")
    return process_whatsapp_message(
        from_number=from_number,
        body=body_text,
        household_id=household_id,
    )


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """AWS Lambda entrypoint for Twilio inbound WhatsApp webhook."""
    headers = event.get("headers") or {}
    # Case-insensitive headers lookup
    headers_lower = {k.lower(): v for k, v in headers.items()}

    raw_body = event.get("body", "")
    if event.get("isBase64Encoded") and isinstance(raw_body, str):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to decode base64 webhook body: {e}")

    # Parse x-www-form-urlencoded
    form_data: Dict[str, str] = {}
    if isinstance(raw_body, dict):
        form_data = {str(k): str(v) for k, v in raw_body.items()}
    elif isinstance(raw_body, str) and raw_body:
        parsed = urllib.parse.parse_qs(raw_body, keep_blank_values=True)
        form_data = {k: v[0] if v else "" for k, v in parsed.items()}

    if settings.TWILIO_VALIDATE_SIGNATURE:
        signature = headers_lower.get("x-twilio-signature", "")
        path = event.get("rawPath") or event.get("path") or ""
        query = event.get("rawQueryString") or urllib.parse.urlencode(event.get("multiValueQueryStringParameters") or {}, doseq=True)
        url = f"{headers_lower.get('x-forwarded-proto', 'https')}://{headers_lower.get('host', '')}{path}"
        if query:
            url += f"?{query}"
        allowed_sender = format_whatsapp_number(settings.GUARDIAN_WHATSAPP_TO)
        sender = format_whatsapp_number(form_data.get("From", ""))
        valid = False
        try:
            from twilio.request_validator import RequestValidator
            if settings.TWILIO_AUTH_TOKEN and signature and allowed_sender and sender == allowed_sender:
                valid = RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, form_data, signature)
        except (ImportError, ValueError):
            logger.warning("Twilio webhook signature could not be verified")
        if not valid:
            return {"statusCode": 403, "headers": {"Content-Type": "text/plain"}, "body": "Forbidden"}

    reply_text = process_whatsapp_webhook(form_data)
    twiml_xml = format_twiml_response(reply_text)

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/xml",
        },
        "body": twiml_xml,
    }
