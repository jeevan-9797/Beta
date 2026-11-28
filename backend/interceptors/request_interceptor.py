"""Bedrock AgentCore Gateway Request Interceptor.

Handles AWS AgentCore Gateway REQUEST interceptor event shape:
- Reads event["mcp"]["gatewayRequest"]["body"] (JSON-RPC tools/call).
- Enforces strip-then-overwrite security rule on the 7 security facts.
- Computes fresh truth from DynamoDB:
    * payee_known (biller/payee registered)
    * hour_ist (0-23 in Asia/Kolkata)
    * payments_last_hour (< 2 limit)
    * frozen (from RULE#CONFIG)
    * approval_valid (valid, matching amount/payee, cooling-off elapsed)
    * assessment_valid (unused, age <= 300s, risk != R4)
    * scam_flag (R4 assessment OR backup keywords)
- Writes append-only AUDIT#<ts> row.
- Returns transformedGatewayRequest format.
"""

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import logging
import re
from uuid import uuid4
from typing import Any, Dict, List, Optional, Tuple

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    put_item,
    get_household_rules,
    get_household_config,
    get_payees,
    get_billers,
    get_approval,
    get_assessment,
    get_receipt,
    query_by_prefix,
)
from backend.common.models import SKPrefix, SecurityFacts

logger = logging.getLogger(__name__)

# Security facts that MUST NEVER be supplied by the model
SECURITY_FIELDS = [
    "within_auto_limit",
    "idempotent_replay",
    "payee_known",
    "hour_ist",
    "payments_last_hour",
    "approval_valid",
    "scam_flag",
    "frozen",
    "assessment_valid",
]

def get_backup_scam_keywords() -> List[str]:
    """Retrieve keywords from src.shield_mcp.keywords if available, else built-in fallback."""
    try:
        from src.shield_mcp.keywords import get_shield_keywords
        return get_shield_keywords()
    except (ImportError, ModuleNotFoundError):
        return [
            "cbi",
            "digital arrest",
            "parcel",
            "customs",
            "otp",
            "pin",
            "secret",
            "police",
            "arrest",
            "kisi ko batana mat",
            "kyc",
            "apk",
            "lottery",
        ]


# Backup keywords for Indian elderly coercion / fraud detection
BACKUP_SCAM_KEYWORDS = get_backup_scam_keywords()


def get_hour_ist(dt: Optional[datetime] = None) -> int:
    """Calculate current hour in Asia/Kolkata timezone (UTC+5:30)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    ist_time = dt.astimezone(ist_offset)
    return ist_time.hour


def parse_timestamp(ts: Any) -> Optional[datetime]:
    """Parse ISO timestamp string to UTC datetime."""
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, timezone.utc)
        clean_ts = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def count_payments_last_hour(household_id: str, now: Optional[datetime] = None) -> int:
    """Count completed payments recorded within the past 60 minutes."""
    if now is None:
        now = datetime.now(timezone.utc)
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    pk = format_pk(household_id)

    payments = query_by_prefix(pk, SKPrefix.PAYMENT)
    recent_count = 0
    for p in payments:
        tx_time = p.get("timestamp") or p.get("created_at") or ""
        if tx_time >= one_hour_ago and p.get("status") in (
            "SUCCESS",
            "COMPLETED",
            "EXECUTED",
            "PAID",
        ):
            recent_count += 1
    return recent_count


def verify_payee_known(household_id: str, payee_id: Optional[str], bill_id: Optional[str]) -> bool:
    """Check whether payee_id or bill_id's biller exists under PAYEE# or BILLER#."""
    pk = format_pk(household_id)

    # 1. Check direct payee_id
    if payee_id:
        target_payee = str(payee_id).strip().upper()
        if get_item(pk, f"{SKPrefix.PAYEE}{target_payee}"):
            return True
        if get_item(pk, f"{SKPrefix.BILLER}{target_payee}"):
            return True
        for p in get_payees(pk):
            if p.get("payee_id", "").upper() == target_payee or p.get("name", "").upper() == target_payee:
                return True
        for b in get_billers(pk):
            if b.get("biller_id", "").upper() == target_payee or b.get("name", "").upper() == target_payee:
                return True

    # 2. Check direct bill_id
    if bill_id:
        target_bill = str(bill_id).strip().upper()
        if get_item(pk, f"{SKPrefix.BILLER}{target_bill}"):
            return True
        for b in get_billers(pk):
            if b.get("bill_id", "").upper() == target_bill or b.get("biller_id", "").upper() == target_bill:
                return True

    return False


def verify_approval_valid(
    household_id: str,
    approval_id: Optional[str],
    amount: float,
    payee_id: Optional[str],
    bill_id: Optional[str],
    now: Optional[datetime] = None,
) -> bool:
    """True ONLY if approval exists, is APPROVED, not expired/cancelled, matching, and cooling-off elapsed."""
    if not approval_id:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_epoch = int(now.timestamp())

    approval = get_approval(household_id, approval_id)
    if not approval:
        return False

    status = approval.get("status")
    if status != "APPROVED":
        return False

    # Check expiration via TTL epoch or expires_at ISO
    ttl = approval.get("ttl")
    if ttl is not None and now_epoch > int(ttl):
        return False
    expires_at = approval.get("expires_at")
    if expires_at and now_iso > str(expires_at):
        return False

    # Verify amount matches (allowing small float epsilon)
    app_amount = float(approval.get("amount", -1))
    if abs(app_amount - amount) > 0.01:
        return False

    # Verify payee / biller match
    app_payee = str(approval.get("payee_name") or approval.get("payee_id") or "").strip().upper()
    target_payee = str(payee_id or "").strip().upper()
    target_bill = str(bill_id or "").strip().upper()
    if target_payee and app_payee:
        if app_payee != target_payee and target_payee not in app_payee and app_payee not in target_payee:
            return False
    elif target_bill and app_payee:
        if app_payee != target_bill and target_bill not in app_payee:
            return False

    # Check cooling-off period / executable_at
    executable_at = approval.get("executable_at")
    if executable_at is not None:
        if now_epoch < int(executable_at):
            return False
    else:
        cooling_off_until = approval.get("cooling_off_until")
        if cooling_off_until:
            if now_iso < str(cooling_off_until):
                return False
        else:
            approved_at_val = (
                approval.get("approved_at")
                or approval.get("responded_at")
                or approval.get("requested_at")
            )
            cooling_off_sec = int(approval.get("cooling_off_seconds", settings.COOLING_OFF_SECONDS))
            if isinstance(approved_at_val, (int, float)):
                if now_epoch < int(approved_at_val) + cooling_off_sec:
                    return False
            elif approved_at_val:
                approved_at = parse_timestamp(str(approved_at_val))
                if approved_at and now < (approved_at + timedelta(seconds=cooling_off_sec)):
                    return False

    return True


def verify_assessment_valid(
    household_id: str,
    assessment_id: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """True ONLY if assessment exists, used is False, not expired (now <= expires_at or age <= 300s), and tier/risk != R4."""
    if not assessment_id:
        return False, None
    if now is None:
        now = datetime.now(timezone.utc)

    assessment = get_assessment(household_id, assessment_id)
    if not assessment:
        return False, None

    # Check used / consumed flag
    if assessment.get("used") is True or assessment.get("consumed") is True or assessment.get("status") == "CONSUMED":
        return False, assessment

    # Check expiration via expires_at if present
    expires_at_str = assessment.get("expires_at")
    if expires_at_str:
        expires_at = parse_timestamp(expires_at_str)
        if expires_at and now > expires_at:
            return False, assessment
    else:
        # Check age <= 300s
        created_at_str = assessment.get("created_at") or assessment.get("timestamp")
        created_at = parse_timestamp(created_at_str)
        if not created_at:
            return False, assessment
        age_seconds = (now - created_at).total_seconds()
        if age_seconds < 0 or age_seconds > 300:
            return False, assessment

    # Check tier != R4 and risk_level != R4 (Jyotir's schema)
    tier = str(assessment.get("tier", "")).strip().upper()
    risk_level = str(assessment.get("risk_level", "")).strip().upper()
    if tier == "R4" or risk_level == "R4":
        return False, assessment

    return True, assessment


def check_scam_flag(
    assessment: Optional[Dict[str, Any]],
    text_corpus: str,
) -> bool:
    """True if assessment tier or risk_level is R4 OR backup keywords match note/arguments."""
    if assessment:
        tier = str(assessment.get("tier", "")).strip().upper()
        risk_level = str(assessment.get("risk_level", "")).strip().upper()
        if tier == "R4" or risk_level == "R4":
            return True

    keywords = get_backup_scam_keywords()
    lower_text = text_corpus.lower()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", lower_text):
            return True

    return False


def detect_scam_keywords(text: str) -> bool:
    """Scan text for high-risk Indian senior citizen scam and extortion keywords."""
    return check_scam_flag(None, text)


def get_current_hour_ist(dt: Optional[datetime] = None) -> int:
    """Calculate current hour in Asia/Kolkata timezone (UTC+5:30)."""
    return get_hour_ist(dt)


def compute_security_facts(
    household_id: str,
    arguments: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compute the security facts directly from DynamoDB."""
    if now is None:
        now = datetime.now(timezone.utc)

    hh_id = format_pk(household_id)
    payee_id = (
        arguments.get("payee_id")
        or arguments.get("pharmacy_name")
        or ("Apollo Pharmacy" if arguments.get("med_ids") or arguments.get("medicine_ids") else None)
    )
    bill_id = arguments.get("bill_id")
    amount = float(arguments.get("amount", 0.0))
    approval_id = arguments.get("approval_id")
    assessment_id = arguments.get("assessment_id")
    note = str(arguments.get("note") or "")

    # 1. payee_known
    payee_known = verify_payee_known(hh_id, payee_id, bill_id)

    # 2. hour_ist (0-23 in Asia/Kolkata)
    hour_ist = get_hour_ist(now)

    # 3. payments_last_hour
    payments_last_hour = count_payments_last_hour(hh_id, now)

    # 4. frozen (from RULE#CONFIG)
    config = get_household_config(hh_id) or {}
    frozen = bool(config.get("frozen", False))
    auto_limit = min(float(config.get("auto_limit", 2000)), float(config.get("max_with_approval", 25000)), 25000)

    # 5. approval_valid
    approval_valid = verify_approval_valid(hh_id, approval_id, amount, payee_id, bill_id, now)

    # 6. assessment_valid
    assessment_valid, assessment_item = verify_assessment_valid(hh_id, assessment_id, now)

    # 7. scam_flag
    text_corpus = f"{note} " + " ".join(str(v) for v in arguments.values())
    scam_flag = check_scam_flag(assessment_item, text_corpus)

    return {
        "payee_known": payee_known,
        "hour_ist": hour_ist,
        "payments_last_hour": payments_last_hour,
        "approval_valid": approval_valid,
        "scam_flag": scam_flag,
        "frozen": frozen,
        "assessment_valid": assessment_valid,
        "within_auto_limit": amount <= auto_limit,
    }


def _is_completed_replay(household_id: str, tool_name: str, arguments: Dict[str, Any]) -> bool:
    tool = tool_name.rsplit("___", 1)[-1]
    keys = {"pay_bill": ("MOCK_BBPS", ("bill_id", "payee_id")),
            "reorder_medicine": ("MOCK_PHARMACY", ("med_ids",))}
    idem_key, assessment_id = arguments.get("idem_key"), arguments.get("assessment_id")
    if tool not in keys or not isinstance(idem_key, str) or not idem_key or not assessment_id:
        return False
    payment = get_item(household_id, f"{SKPrefix.PAYMENT}{idem_key}") or {}
    provider, fields = keys[tool]
    if (payment.get("status") != "SUCCESS" or payment.get("provider") != provider
            or payment.get("assessment_id") != assessment_id or payment.get("amount") != arguments.get("amount")
            or any(payment.get(key) != arguments.get(key) for key in fields)):
        return False
    if tool == "reorder_medicine" and payment.get("payee_name") != (arguments.get("pharmacy_name") or "Apollo Pharmacy"):
        return False
    return bool(payment.get("tx_id") and get_receipt(household_id, payment["tx_id"]))


def process_gateway_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process Bedrock AgentCore Gateway REQUEST interceptor event.

    Event shape: event["mcp"]["gatewayRequest"]["body"] (JSON-RPC tools/call).
    Returns: {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"headers": headers, "body": updated_body}}}
    """
    mcp_block = event.get("mcp", {})
    gateway_request = mcp_block.get("gatewayRequest", {})
    headers = gateway_request.get("headers", {})
    raw_body = gateway_request.get("body", {})

    is_body_str = isinstance(raw_body, str)
    if is_body_str:
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}
    else:
        body = deepcopy(raw_body)

    # Locate JSON-RPC params.arguments
    params = body.get("params", {})
    tool_name = params.get("name") or body.get("tool_name") or ""
    arguments = params.get("arguments") if "arguments" in params else body.get("arguments", {})

    # Capture original args for audit before strip-then-overwrite
    original_args = deepcopy(arguments)

    # CRITICAL SECURITY RULE: Unconditionally DELETE model-supplied security fields
    for field in SECURITY_FIELDS:
        arguments.pop(field, None)

    household_id = arguments.get("household_id") or settings.DEFAULT_HOUSEHOLD_ID
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Compute fresh truth from DynamoDB
    computed_facts = compute_security_facts(household_id, arguments, now=now)
    computed_facts["idempotent_replay"] = _is_completed_replay(household_id, tool_name, arguments)

    # Inject computed facts into arguments (context.input in Cedar)
    arguments.update(computed_facts)

    # Write append-only AUDIT#<ts> row
    audit_row = {
        "PK": format_pk(household_id),
        "SK": f"{SKPrefix.AUDIT}{now_iso}#{uuid4().hex}",
        "action": f"INTERCEPTOR_EVALUATED:{tool_name}",
        "actor": "SYSTEM:REQUEST_INTERCEPTOR",
        "tool_name": tool_name,
        "mcp_message_id": body.get("id"),
        "original_args": original_args,
        "computed_facts": computed_facts,
        "status": "EVALUATED",
        "timestamp": now_iso,
        "entity_type": "AUDIT",
    }
    put_item(audit_row)

    # Reconstruct updated body
    if "arguments" in params:
        params["arguments"] = arguments
        body["params"] = params
    else:
        body["arguments"] = arguments

    updated_body = json.dumps(body) if is_body_str else body

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": headers,
                "body": updated_body,
            }
        },
    }


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """AWS Lambda entrypoint for AgentCore Gateway Request Interceptor."""
    return process_gateway_request(event)


class RequestInterceptor:
    """AgentCore Gateway Request Interceptor class."""

    def __init__(self, household_id: Optional[str] = None):
        self.household_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)

    def extract_security_facts(
        self,
        user_prompt: str = "",
        target_payee: str = "",
        approval_id: Optional[str] = None,
        assessment_id: Optional[str] = None,
        amount: float = 0.0,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> SecurityFacts:
        args = dict(arguments or {})
        if target_payee and not args.get("payee_id"):
            args["payee_id"] = target_payee
        if approval_id and not args.get("approval_id"):
            args["approval_id"] = approval_id
        if assessment_id and not args.get("assessment_id"):
            args["assessment_id"] = assessment_id
        if amount and not args.get("amount"):
            args["amount"] = amount
        if user_prompt and not args.get("note"):
            args["note"] = user_prompt

        facts = compute_security_facts(self.household_id, args)
        return SecurityFacts(
            payee_known=facts["payee_known"],
            hour_ist=facts["hour_ist"],
            payments_last_hour=facts["payments_last_hour"],
            approval_valid=facts["approval_valid"],
            scam_flag=facts["scam_flag"],
            frozen=facts["frozen"],
            assessment_valid=facts["assessment_valid"],
        )

    def intercept(
        self,
        request_context: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        user_prompt = request_context.get("prompt", "")
        tool_name = request_context.get("tool_name", "")
        tool_args = dict(request_context.get("tool_args", {}))

        facts = self.extract_security_facts(
            user_prompt=user_prompt,
            target_payee=tool_args.get("payee_id") or tool_args.get("biller_id", ""),
            approval_id=tool_args.get("approval_id"),
            assessment_id=tool_args.get("assessment_id"),
            amount=float(tool_args.get("amount", 0)),
            arguments=tool_args,
        )

        injected_context = {
            "household_id": self.household_id,
            "security_facts": facts.to_dict(),
        }

        # Guardrail checks
        if facts.frozen:
            return False, injected_context, "Action blocked: Household transactions are frozen."
        if facts.scam_flag:
            return False, injected_context, "Action blocked: Coercion or scam flag detected."
        if not facts.assessment_valid:
            return False, injected_context, "Action blocked: Voice assessment is missing, stale, consumed, or invalid."

        rules = get_household_rules(self.household_id) or {}
        auto_limit = float(rules.get("auto_limit", 2000.0))
        amount = float(tool_args.get("amount", 0))

        if amount > auto_limit and not facts.approval_valid:
            return False, injected_context, f"Approval required: Amount ₹{amount} exceeds auto_limit ₹{auto_limit}."
        if not facts.payee_known:
            return False, injected_context, "Action blocked: Payee is not on the whitelist."

        return True, injected_context, None
