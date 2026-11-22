"""Bedrock AgentCore Gateway Request Interceptor.

Evaluates security posture, injects the 6 mandatory security facts,
enforces Cedar/DynamoDB guardrail policies, cooling-off delays, night curfews,
and prevents financial exploitation or scam coercion.
"""

from datetime import datetime, timezone, timedelta
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_household_rules,
    get_payees,
    get_billers,
    get_approval,
    query_by_prefix,
)
from backend.common.models import SKPrefix, SecurityFacts

logger = logging.getLogger(__name__)

# Common Indian scam & coercion triggers targeted at senior citizens
SCAM_TRIGGERS = [
    r"digital arrest",
    r"arrest warrant",
    r"cbi officer",
    r"police verification",
    r"police",
    r"customs clearance",
    r"kyc expired",
    r"kyc suspended",
    r"sim card block",
    r"electricity disconnect today",
    r"lottery winner",
    r"install anydesk",
    r"install teamviewer",
    r"download apk",
    r"share otp",
    r"urgent transfer",
    r"transfer money",
]


def get_current_hour_ist(dt: Optional[datetime] = None) -> int:
    """Calculate current hour in Indian Standard Time (UTC+5:30)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    ist_offset = timedelta(hours=5, minutes=30)
    ist_time = dt + ist_offset
    return ist_time.hour


def detect_scam_keywords(text: str) -> bool:
    """Scan transcript or user input for high-risk Indian senior citizen scam patterns."""
    if not text:
        return False
    lower_text = text.lower()
    for pattern in SCAM_TRIGGERS:
        if re.search(pattern, lower_text):
            return True
    return False


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
        if tx_time >= one_hour_ago and p.get("status") in ("SUCCESS", "COMPLETED", "EXECUTED"):
            recent_count += 1
    return recent_count


def is_payee_known(household_id: str, payee_name_or_id: str) -> bool:
    """Verify if the merchant, utility, or payee is registered and trusted in DynamoDB."""
    if not payee_name_or_id:
        return False
    target = payee_name_or_id.strip().upper()
    pk = format_pk(household_id)

    # Check registered billers (BESCOM, BWSSB, etc.)
    billers = get_billers(pk)
    for b in billers:
        if (
            b.get("biller_id", "").upper() == target
            or b.get("name", "").upper() == target
        ):
            return True

    # Check saved payees (Apollo Pharmacy, etc.)
    payees = get_payees(pk)
    for p in payees:
        if (
            p.get("payee_id", "").upper() == target
            or p.get("name", "").upper() == target
            or target in p.get("name", "").upper()
        ):
            return True

    return False


def is_approval_valid(household_id: str, approval_id: Optional[str]) -> bool:
    """Validate whether an approval is approved, not expired, and past cooling-off."""
    if not approval_id:
        return False
    approval = get_approval(household_id, approval_id)
    if not approval:
        return False

    status = approval.get("status")
    if status != "APPROVED":
        return False

    now_iso = datetime.now(timezone.utc).isoformat()

    # Check cooling-off period completion
    cooling_off_until = approval.get("cooling_off_until")
    if cooling_off_until and now_iso < cooling_off_until:
        return False

    # Check expiry
    expires_at = approval.get("expires_at")
    if expires_at and now_iso > expires_at:
        return False

    return True


class RequestInterceptor:
    """AgentCore Gateway Request Interceptor for security validation and facts injection."""

    def __init__(self, household_id: Optional[str] = None):
        self.household_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)

    def extract_security_facts(
        self,
        user_prompt: str,
        target_payee: str = "",
        approval_id: Optional[str] = None,
    ) -> SecurityFacts:
        """Compute the 6 mandatory security facts dynamically from state and request context."""
        rules = get_household_rules(self.household_id) or {}
        frozen = bool(rules.get("frozen", False))
        hour_ist = get_current_hour_ist()
        payments_last_hour = count_payments_last_hour(self.household_id)
        scam_flag = detect_scam_keywords(user_prompt)
        payee_known = is_payee_known(self.household_id, target_payee) if target_payee else True
        approval_valid = is_approval_valid(self.household_id, approval_id)

        return SecurityFacts(
            payee_known=payee_known,
            hour_ist=hour_ist,
            payments_last_hour=payments_last_hour,
            approval_valid=approval_valid,
            scam_flag=scam_flag,
            frozen=frozen,
        )

    def intercept(
        self,
        request_context: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """Intercept tool request and apply Cedar & DynamoDB security guardrails.

        Returns:
            (allow: bool, injected_context: Dict[str, Any], block_reason: Optional[str])
        """
        user_prompt = request_context.get("prompt", "")
        tool_name = request_context.get("tool_name", "")
        tool_args = request_context.get("tool_args", {})
        target_payee = (
            tool_args.get("payee_name")
            or tool_args.get("biller_id")
            or tool_args.get("payee_id")
            or ""
        )
        amount = float(tool_args.get("amount", 0.0))
        approval_id = tool_args.get("approval_id")

        facts = self.extract_security_facts(
            user_prompt=user_prompt,
            target_payee=target_payee,
            approval_id=approval_id,
        )

        injected_context = {
            "household_id": self.household_id,
            "security_facts": facts.to_dict(),
        }

        # Rule evaluation
        rules = get_household_rules(self.household_id) or {}
        auto_limit = float(rules.get("auto_limit", 2000.0))
        max_with_approval = float(rules.get("max_with_approval", 25000.0))
        night_start = int(rules.get("night_start", 23))
        night_end = int(rules.get("night_end", 6))
        max_payments_per_hour = int(rules.get("max_payments_per_hour", 2))

        # 1. Kill Switch / Frozen check
        if facts.frozen:
            msg = "Action blocked: Household transactions are currently frozen by Guardian."
            log_audit(
                self.household_id,
                action="REQUEST_BLOCKED_FROZEN",
                actor="SYSTEM:GATEWAY",
                details={"tool": tool_name, "reason": msg},
                risk_score=1.0,
                status="BLOCKED",
            )
            return False, injected_context, msg

        # 2. Scam coercion check
        if facts.scam_flag:
            msg = "High risk detected: Suspicious urgency or scam keywords detected in voice interaction."
            log_audit(
                self.household_id,
                action="REQUEST_BLOCKED_SCAM",
                actor="SYSTEM:SHIELD",
                details={"prompt": user_prompt, "tool": tool_name},
                risk_score=0.95,
                status="BLOCKED",
            )
            return False, injected_context, msg

        # 3. Night Curfew check for financial transactions
        is_night = (
            facts.hour_ist >= night_start or facts.hour_ist < night_end
            if night_start > night_end
            else night_start <= facts.hour_ist < night_end
        )
        if is_night and tool_name in ("pay_utility_bill", "reorder_medicines", "execute_payment"):
            msg = f"Action blocked: Financial transactions are prohibited during night curfew ({night_start}:00 to {night_end}:00 IST)."
            log_audit(
                self.household_id,
                action="REQUEST_BLOCKED_NIGHT_CURFEW",
                actor="SYSTEM:GATEWAY",
                details={"hour_ist": facts.hour_ist, "tool": tool_name},
                risk_score=0.8,
                status="BLOCKED",
            )
            return False, injected_context, msg

        # 4. Hourly transaction rate limit
        if facts.payments_last_hour >= max_payments_per_hour and tool_name in (
            "pay_utility_bill",
            "reorder_medicines",
            "execute_payment",
        ):
            msg = f"Action blocked: Maximum payments per hour ({max_payments_per_hour}) exceeded."
            log_audit(
                self.household_id,
                action="REQUEST_BLOCKED_RATE_LIMIT",
                actor="SYSTEM:GATEWAY",
                details={"payments_last_hour": facts.payments_last_hour},
                risk_score=0.7,
                status="BLOCKED",
            )
            return False, injected_context, msg

        # 5. Financial amount checks
        if tool_name in ("pay_utility_bill", "reorder_medicines", "execute_payment"):
            if amount > max_with_approval:
                msg = f"Action blocked: Requested amount (₹{amount}) exceeds absolute ceiling of ₹{max_with_approval}."
                log_audit(
                    self.household_id,
                    action="REQUEST_BLOCKED_EXCEEDS_CEILING",
                    actor="SYSTEM:GATEWAY",
                    details={"amount": amount, "ceiling": max_with_approval},
                    risk_score=0.85,
                    status="BLOCKED",
                )
                return False, injected_context, msg

            if amount > auto_limit and not facts.approval_valid:
                msg = f"Approval required: Amount ₹{amount} exceeds auto-limit of ₹{auto_limit}. Requires Guardian WhatsApp approval."
                log_audit(
                    self.household_id,
                    action="APPROVAL_REQUIRED",
                    actor="SYSTEM:GATEWAY",
                    details={"amount": amount, "auto_limit": auto_limit},
                    risk_score=0.3,
                    status="PENDING_APPROVAL",
                )
                return False, injected_context, msg

            if not facts.payee_known:
                msg = f"Unknown payee: '{target_payee}' is not on the approved whitelist for this household."
                log_audit(
                    self.household_id,
                    action="REQUEST_BLOCKED_UNKNOWN_PAYEE",
                    actor="SYSTEM:GATEWAY",
                    details={"payee": target_payee},
                    risk_score=0.9,
                    status="BLOCKED",
                )
                return False, injected_context, msg

        return True, injected_context, None
