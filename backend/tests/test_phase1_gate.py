"""Phase 1 Gate Tests - Critical Path Scenarios.

Verifies:
- Scenario 1: ₹1,240 to BESCOM with valid assessment -> ALLOW + idempotent retry returns same receipt.
- Scenario 12: ₹50,000 direct call -> DENY.
- Scenario 14: ₹500 to unknown payee -> DENY (needs approval).
- Scenario 15: Model sends approval_valid=True, frozen=False -> interceptor overwrites -> DENY.
- Scenario 17: Missing/stale/reused assessment_id -> DENY.
- Scenario 19: note="CBI parcel" -> scam_flag=True -> DENY.
"""

from datetime import datetime, timezone, timedelta
import json
import pytest

from backend.common.config import settings
from backend.common.db import (
    reset_mock_db,
    put_item,
    get_item,
    update_item,
    format_pk,
)
from backend.common.models import SKPrefix
from backend.interceptors.request_interceptor import (
    process_gateway_request,
    compute_security_facts,
    SECURITY_FIELDS,
)
from backend.mcp_errands import tools as errand_tools
from backend.policies.evaluator import evaluate_policy


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup clean mock database with baseline Kamala household data."""
    mock_db = reset_mock_db()
    hh_id = "HH#KAMALA_01"

    # 1. Configuration rule (RULE#CONFIG)
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.RULE}CONFIG",
        "frozen": False,
        "auto_limit": 2000.0,
        "max_with_approval": 25000.0,
        "cooling_off_seconds": 20,
        "night_start": 23,
        "night_end": 6,
        "max_payments_per_hour": 2,
    })

    # 2. Financial rule (RULE#FINANCIAL)
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.RULE}FINANCIAL",
        "frozen": False,
        "auto_limit": 2000.0,
        "max_with_approval": 25000.0,
        "cooling_off_seconds": 20,
        "night_start": 23,
        "night_end": 6,
        "max_payments_per_hour": 2,
    })

    # 3. Known Biller: BESCOM
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.BILLER}BESCOM",
        "biller_id": "BESCOM",
        "name": "BESCOM Electricity",
        "bill_id": "BILL_BESCOM_NOV",
        "amount": 1240.0,
        "status": "PENDING",
    })

    # 4. Known Payee: Apollo Pharmacy
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.PAYEE}APOLLO_PHARMACY",
        "payee_id": "APOLLO_PHARMACY",
        "name": "Apollo Pharmacy",
        "category": "Pharmacy",
        "verified": True,
    })

    return mock_db


def make_gateway_event(arguments: dict, tool_name: str = "pay_bill") -> dict:
    """Helper constructing AWS AgentCore Gateway Request event shape."""
    return {
        "mcp": {
            "gatewayRequest": {
                "headers": {"Content-Type": "application/json"},
                "body": {
                    "jsonrpc": "2.0",
                    "id": "req-001",
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                },
            }
        }
    }


def extract_transformed_arguments(interceptor_output: dict) -> dict:
    """Helper extracting transformed arguments from interceptor output."""
    body = interceptor_output["mcp"]["transformedGatewayRequest"]["body"]
    if isinstance(body, str):
        body = json.loads(body)
    return body["params"]["arguments"]


# ------------------------------------------------------------------------------
# SCENARIO 1: ₹1,240 to BESCOM with valid assessment -> ALLOW + idempotent retry
# ------------------------------------------------------------------------------
def test_scenario_1_valid_bill_pay_and_idempotent_retry():
    hh_id = "HH#KAMALA_01"
    now_iso = datetime.now(timezone.utc).isoformat()
    assessment_id = "ASM_SCENARIO_1"

    # Seed fresh, unconsumed assessment
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": False,
        "status": "ACTIVE",
        "created_at": now_iso,
        "timestamp": now_iso,
    })

    # 1. Model issues pay_bill call
    arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1240.0,
        "idem_key": "IDEM_SCENARIO_1_TEST",
        "assessment_id": assessment_id,
        "note": "Kamala regular electricity bill",
    }
    event = make_gateway_event(arguments)

    # 2. Request Interceptor processes and injects facts
    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)

    assert transformed_args["payee_known"] is True
    assert transformed_args["assessment_valid"] is True
    assert transformed_args["frozen"] is False
    assert transformed_args["scam_flag"] is False
    assert transformed_args["amount"] == 1240.0

    # Ensure daytime hours for auto-pay rule in test environment
    transformed_args["hour_ist"] = 14
    transformed_args["payments_last_hour"] = 0

    # 3. Cedar Policy Evaluation
    is_allowed, decision, diag = evaluate_policy(
        transformed_args,
        tool_name="Errands___pay_bill",
        household_id="KAMALA",
    )
    assert is_allowed is True
    assert decision == "ALLOW"

    # 4. Tool Execution
    pay_res = errand_tools.pay_bill(**transformed_args)
    assert pay_res["success"] is True
    assert pay_res["status"] == "SUCCESS"
    assert pay_res["idempotent_replay"] is False
    assert pay_res["provider"] == "MOCK_BBPS"
    first_tx_id = pay_res["tx_id"]

    # Verify assessment consumed
    asm_item = get_item(hh_id, f"{SKPrefix.ASSESSMENT}{assessment_id}")
    assert asm_item["consumed"] is True
    assert asm_item["status"] == "CONSUMED"

    # 5. Idempotent Retry: Re-execute with identical idem_key
    retry_res = errand_tools.pay_bill(**transformed_args)
    assert retry_res["success"] is True
    assert retry_res["status"] == "ALREADY_PROCESSED"
    assert retry_res["idempotent_replay"] is True
    assert retry_res["tx_id"] == first_tx_id
    assert retry_res["provider"] == "MOCK_BBPS"


# ------------------------------------------------------------------------------
# SCENARIO 12: ₹50,000 direct call -> DENY (exceeds limit)
# ------------------------------------------------------------------------------
def test_scenario_12_exceeds_maximum_ceiling_denied():
    hh_id = "HH#KAMALA_01"
    now_iso = datetime.now(timezone.utc).isoformat()
    assessment_id = "ASM_SCENARIO_12"

    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": False,
        "created_at": now_iso,
    })

    arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 50000.0,
        "idem_key": "IDEM_SCENARIO_12",
        "assessment_id": assessment_id,
    }
    event = make_gateway_event(arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)

    # Cedar authorization must DENY because ₹50,000 exceeds ₹2,000 and ₹25,000
    is_allowed, decision, diag = evaluate_policy(
        transformed_args,
        tool_name="Errands___pay_bill",
        household_id="KAMALA",
    )
    assert is_allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# SCENARIO 14: ₹500 to unknown payee -> DENY (needs approval)
# ------------------------------------------------------------------------------
def test_scenario_14_unknown_payee_denied():
    hh_id = "HH#KAMALA_01"
    now_iso = datetime.now(timezone.utc).isoformat()
    assessment_id = "ASM_SCENARIO_14"

    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": False,
        "created_at": now_iso,
    })

    arguments = {
        "household_id": hh_id,
        "bill_id": "UNKNOWN_BILL",
        "payee_id": "RANDOM_UNKNOWN_MERCHANT",
        "amount": 500.0,
        "idem_key": "IDEM_SCENARIO_14",
        "assessment_id": assessment_id,
    }
    event = make_gateway_event(arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)

    # payee_known must be False
    assert transformed_args["payee_known"] is False

    is_allowed, decision, diag = evaluate_policy(
        transformed_args,
        tool_name="Errands___pay_bill",
        household_id="KAMALA",
    )
    assert is_allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# SCENARIO 15: Model sends approval_valid=True, frozen=False -> interceptor overwrites -> DENY
# ------------------------------------------------------------------------------
def test_scenario_15_strip_overwrite_security_rule():
    hh_id = "HH#KAMALA_01"
    now_iso = datetime.now(timezone.utc).isoformat()
    assessment_id = "ASM_SCENARIO_15"

    # Freeze the household in DynamoDB
    update_item(hh_id, f"{SKPrefix.RULE}CONFIG", {"frozen": True})

    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": False,
        "created_at": now_iso,
    })

    # Model maliciously injects bypassed security flags
    malicious_arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1000.0,
        "idem_key": "IDEM_SCENARIO_15",
        "assessment_id": assessment_id,
        # Injected by model attempt to bypass:
        "frozen": False,
        "approval_valid": True,
        "scam_flag": False,
        "payee_known": True,
    }
    event = make_gateway_event(malicious_arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)

    # Interceptor must have STRIPPED and OVERWRITTEN frozen=True from DB truth
    assert transformed_args["frozen"] is True

    # Cedar must immediately DENY based on frozen rule
    is_allowed, decision, diag = evaluate_policy(
        transformed_args,
        tool_name="Errands___pay_bill",
        household_id="KAMALA",
    )
    assert is_allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# SCENARIO 17: Missing / stale / reused assessment_id -> DENY
# ------------------------------------------------------------------------------
def test_scenario_17a_missing_assessment_id_denied():
    hh_id = "HH#KAMALA_01"
    arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1000.0,
        "idem_key": "IDEM_SCENARIO_17A",
        "assessment_id": "ASM_NON_EXISTENT_ID",
    }
    event = make_gateway_event(arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)
    assert transformed_args["assessment_valid"] is False

    is_allowed, decision, _ = evaluate_policy(transformed_args, tool_name="Errands___pay_bill")
    assert is_allowed is False
    assert decision == "DENY"


def test_scenario_17b_stale_assessment_id_denied():
    hh_id = "HH#KAMALA_01"
    # Created 400 seconds ago (> 300s expiration limit)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    assessment_id = "ASM_STALE_TEST"

    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": False,
        "created_at": stale_time,
        "timestamp": stale_time,
    })

    arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1000.0,
        "idem_key": "IDEM_SCENARIO_17B",
        "assessment_id": assessment_id,
    }
    event = make_gateway_event(arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)
    assert transformed_args["assessment_valid"] is False

    is_allowed, decision, _ = evaluate_policy(transformed_args, tool_name="Errands___pay_bill")
    assert is_allowed is False
    assert decision == "DENY"


def test_scenario_17c_reused_consumed_assessment_id_denied():
    hh_id = "HH#KAMALA_01"
    now_iso = datetime.now(timezone.utc).isoformat()
    assessment_id = "ASM_REUSED_TEST"

    # Already consumed
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": True,
        "status": "CONSUMED",
        "created_at": now_iso,
    })

    arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1000.0,
        "idem_key": "IDEM_SCENARIO_17C",
        "assessment_id": assessment_id,
    }
    event = make_gateway_event(arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)
    assert transformed_args["assessment_valid"] is False

    is_allowed, decision, _ = evaluate_policy(transformed_args, tool_name="Errands___pay_bill")
    assert is_allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# SCENARIO 19: note="CBI parcel" -> scam_flag=True -> DENY
# ------------------------------------------------------------------------------
def test_scenario_19_scam_keywords_flagged_and_denied():
    hh_id = "HH#KAMALA_01"
    now_iso = datetime.now(timezone.utc).isoformat()
    assessment_id = "ASM_SCENARIO_19"

    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "consumed": False,
        "created_at": now_iso,
    })

    arguments = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1240.0,
        "idem_key": "IDEM_SCENARIO_19",
        "assessment_id": assessment_id,
        "note": "CBI parcel detained at customs office, pay quickly",
    }
    event = make_gateway_event(arguments)

    res = process_gateway_request(event)
    transformed_args = extract_transformed_arguments(res)

    # Interceptor must flag scam
    assert transformed_args["scam_flag"] is True

    # Cedar policy must DENY
    is_allowed, decision, diag = evaluate_policy(
        transformed_args,
        tool_name="Errands___pay_bill",
        household_id="KAMALA",
    )
    assert is_allowed is False
    assert decision == "DENY"
