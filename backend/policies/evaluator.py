"""Cedar Policy Evaluation Engine for BETA.

Evaluates beta_policies.cedar using cedarpy. Provides exact Cedar authorization
decisions (ALLOW / DENY) for AgentCore Gateway actions and local gate testing.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import logging

from backend.common.config import settings

logger = logging.getLogger(__name__)

# Try importing cedarpy
try:
    import cedarpy

    CEDARPY_AVAILABLE = True
except ImportError:
    CEDARPY_AVAILABLE = False
    cedarpy = None

DEFAULT_GATEWAY_ARN = "arn:aws:bedrock:ap-south-1:123456789012:gateway/beta-gateway"

GUARDIAN_TOOLS = {
    "request_approval",
    "get_approval_status",
    "notify_guardian",
    "get_guard_status",
    "request_guardian_approval",
    "check_approval_status",
    "send_guardian_alert",
    "get_household_rules",
}

SHIELD_TOOLS = {
    "assess_risk",
    "explain_scam",
    "verify_payee_trust",
    "evaluate_cooling_off",
}

ERRANDS_READ_ONLY_TOOLS = {
    "get_profile",
    "list_billers",
    "list_payees",
    "list_meds",
    "get_due_bills",
    "find_slots",
    "book_appointment",
    "set_reminder",
    "check_bill_status",
    "list_saved_billers",
}

MONEY_ACTIONS = {
    "Errands___pay_bill",
    "Errands___reorder_medicine",
}


def load_policy_text(policy_path: Optional[str] = None) -> str:
    """Load Cedar policy file contents."""
    path = Path(policy_path or settings.CEDAR_POLICY_PATH)
    if not path.is_file():
        # Fallback to local default path
        fallback = Path(__file__).parent / "beta_policies.cedar"
        if fallback.is_file():
            path = fallback
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _sanitize_for_cedar(item: Any) -> Any:
    """Cedar does not support floating point values; convert floats to ints."""
    if isinstance(item, float):
        return int(round(item))
    if isinstance(item, dict):
        return {k: _sanitize_for_cedar(v) for k, v in item.items()}
    if isinstance(item, list):
        return [_sanitize_for_cedar(v) for v in item]
    return item


def evaluate_policy(
    arguments: Dict[str, Any],
    tool_name: str = "Errands___pay_bill",
    household_id: str = "KAMALA",
    gateway_arn: str = DEFAULT_GATEWAY_ARN,
    policy_path: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Evaluate Cedar authorization for given tool call arguments.

    Args:
        arguments: Transformed arguments (containing injected security facts).
        tool_name: Target action in AgentCore format, e.g. 'Errands___pay_bill' or 'pay_bill'.
        household_id: Principal identifier.
        gateway_arn: Resource identifier for AgentCore Gateway.
        policy_path: Path to Cedar policy file.

    Returns:
        (is_allowed: bool, decision: 'ALLOW' | 'DENY', diagnostics: dict)
    """
    # Normalize action name to AgentCore format
    action_raw = tool_name
    if action_raw.startswith('AgentCore::Action::"'):
        canonical_name = action_raw[len('AgentCore::Action::"') : -1]
    elif action_raw.startswith("AgentCore::Action::"):
        canonical_name = action_raw[len("AgentCore::Action::") :]
    else:
        canonical_name = action_raw

    if "___" not in canonical_name:
        if canonical_name in GUARDIAN_TOOLS:
            canonical_name = f"Guardian___{canonical_name}"
        elif canonical_name in SHIELD_TOOLS:
            canonical_name = f"Shield___{canonical_name}"
        else:
            canonical_name = f"Errands___{canonical_name}"

    action_name = f'AgentCore::Action::"{canonical_name}"'

    principal = f'AgentCore::User::"{household_id}"'
    resource = f'AgentCore::Gateway::"{gateway_arn}"'

    policy_text = load_policy_text(policy_path)
    cedar_context_input = _sanitize_for_cedar(arguments)

    request_payload = {
        "principal": principal,
        "action": action_name,
        "resource": resource,
        "context": {
            "input": cedar_context_input,
        },
    }

    if CEDARPY_AVAILABLE:
        try:
            result = cedarpy.is_authorized(
                request=request_payload,
                policies=policy_text,
                entities=[],
            )
            # result.decision is Decision.Allow or Decision.Deny
            decision_str = "ALLOW" if str(result.decision).endswith("Allow") else "DENY"
            is_allowed = decision_str == "ALLOW"
            diagnostics = {
                "decision": decision_str,
                "reasons": getattr(result.diagnostics, "reasons", []),
                "errors": getattr(result.diagnostics, "errors", []),
            }
            return is_allowed, decision_str, diagnostics
        except Exception as e:
            logger.error(f"Error evaluating Cedar policy via cedarpy: {e}")

    # Pure Python fallback rule evaluation if cedarpy is not available or errored
    input_data = arguments
    frozen = bool(input_data.get("frozen", False))
    scam_flag = bool(input_data.get("scam_flag", False))

    is_money_action = canonical_name in MONEY_ACTIONS

    # Forbid rules only apply to money actions
    if is_money_action:
        if frozen:
            return False, "DENY", {"reason": "forbid: frozen == true"}
        if scam_flag:
            return False, "DENY", {"reason": "forbid: scam_flag == true"}

    # Guardian oversight and Shield assessment tools are unconditionally permitted
    short_name = canonical_name.split("___")[-1]
    if (
        canonical_name.startswith("Guardian___")
        or canonical_name.startswith("Shield___")
        or short_name in GUARDIAN_TOOLS
        or short_name in SHIELD_TOOLS
    ):
        return True, "ALLOW", {"reason": "permit: guardian or shield tool"}

    # Baseline read-only / R0-R1 Errand tools
    if (
        short_name in ERRANDS_READ_ONLY_TOOLS
        or any(t in canonical_name for t in ERRANDS_READ_ONLY_TOOLS)
    ):
        return True, "ALLOW", {"reason": "permit: baseline read-only tool"}

    amount = float(input_data.get("amount", 0.0))
    payee_known = bool(input_data.get("payee_known", False))
    assessment_valid = bool(input_data.get("assessment_valid", False))
    hour_ist = int(input_data.get("hour_ist", 12))
    payments_last_hour = int(input_data.get("payments_last_hour", 0))
    approval_valid = bool(input_data.get("approval_valid", False))

    # Rule 3: R2 Auto-Pay (pay_bill and reorder_medicine)
    if (
        is_money_action
        and amount <= 2000
        and payee_known
        and assessment_valid
        and (6 <= hour_ist < 23)
        and (payments_last_hour < 2)
    ):
        return True, "ALLOW", {"reason": "permit: R2 auto-pay"}

    # Rule 4: R3 Approved Pay (pay_bill and reorder_medicine)
    if is_money_action and approval_valid and assessment_valid and amount <= 25000:
        return True, "ALLOW", {"reason": "permit: R3 approved-pay"}

    return False, "DENY", {"reason": "default deny: no permit matched"}
