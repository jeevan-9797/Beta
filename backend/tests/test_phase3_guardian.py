"""Phase 3 Tests - Guardian MCP, Twilio Webhook, Approval State Machine, and Scoped Cedar Policies.

Verifies:
- Scenario 2: Approval workflow for ₹3,800 medicine reorder:
    Request approval -> PENDING -> Arjun replies '1' -> APPROVED_COOLING_OFF (Cedar DENY) ->
    after cooling-off -> READY (Cedar ALLOW) -> reorder succeeds.
- Scenario 3: Guardian declines:
    Request approval -> Arjun replies '2' -> DECLINED -> Cedar DENY.
- Scenario 4: Approval TTL expiration:
    Approval past TTL -> EXPIRED -> Cedar DENY.
- Scenario 16: Cancellation during cooling-off:
    Approved -> Arjun replies 'CANCEL' within cooling-off -> CANCELLED -> Cedar DENY.
- Scoped Policies & Freeze/Unfreeze:
    FREEZE command sets frozen=True -> money actions denied by Cedar, but Guardian
    alerts (notify_guardian) and read-only tools permitted -> UNFREEZE restores normal operations.
- Twilio Webhook Lambda Handler:
    Handles form-urlencoded and base64 bodies, returns TwiML XML.
"""

import base64
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
from backend.common.twilio_client import send_whatsapp_message, format_whatsapp_number
from backend.guardian_api.whatsapp_webhook import (
    process_whatsapp_message,
    process_whatsapp_webhook,
    lambda_handler as webhook_lambda_handler,
    format_twiml_response,
)
from backend.interceptors.request_interceptor import (
    compute_security_facts,
    verify_approval_valid,
    process_gateway_request,
)
from backend.mcp_guardian import tools as guardian_tools
from backend.mcp_guardian import handler as guardian_handler
from backend.mcp_errands import tools as errand_tools
from backend.policies.evaluator import evaluate_policy


@pytest.fixture(autouse=True)
def setup_guardian_environment():
    """Setup mock database with reconciled HH#kamala schema."""
    mock_db = reset_mock_db()
    hh_id = "HH#kamala"
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    now_epoch = int(now_dt.timestamp())

    # 1. Household Members
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.MEMBER}KAMALA",
        "name": "Kamala",
        "role": "PARENT",
        "age": 74,
        "location": "Dharwad",
    })
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.MEMBER}ARJUN",
        "name": "Arjun",
        "role": "GUARDIAN",
        "location": "Bengaluru",
        "phone": "+919876543210",
        "relation": "Son",
    })

    # 2. Config & Rules
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
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.RULE}FINANCIAL",
        "frozen": False,
        "auto_limit": 2000.0,
        "max_with_approval": 25000.0,
        "cooling_off_seconds": 20,
    })

    # 3. Payees & Billers
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.PAYEE}APOLLO_PHARMACY",
        "payee_id": "APOLLO_PHARMACY",
        "name": "Apollo Pharmacy",
        "category": "HEALTHCARE",
        "is_known": True,
    })
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.BILLER}BESCOM",
        "biller_id": "BESCOM",
        "name": "BESCOM",
        "bill_id": "BILL_BESCOM_NOV",
        "amount_due": 1240.0,
        "due_date": "2026-11-28",
        "is_known": True,
    })

    # 4. Medicines
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.MED}METFORMIN_500",
        "med_id": "METFORMIN_500",
        "name": "Metformin 500mg",
        "price": 400.0,
    })
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.MED}AMLODIPINE_5",
        "med_id": "AMLODIPINE_5",
        "name": "Amlodipine 5mg",
        "price": 3400.0,
    })

    # 5. Fresh Voice Assessment (R2 tier, valid, unconsumed)
    put_item({
        "PK": hh_id,
        "SK": "ASSESS#ass_valid_phase3",
        "assessment_id": "ass_valid_phase3",
        "risk_level": "R2",
        "tier": "R2",
        "signals": ["calm_voice", "familiar_request"],
        "used": False,
        "consumed": False,
        "created_at": now_epoch,
        "expires_at": (now_dt + timedelta(minutes=5)).isoformat(),
    })

    return mock_db


# ------------------------------------------------------------------------------
# Unit Tests: Twilio WhatsApp Helper & Outbound Mock
# ------------------------------------------------------------------------------
def test_twilio_client_formatting_and_mock_send():
    """Verify format_whatsapp_number auto-prefixes and send_whatsapp_message returns mock dict."""
    clean_num = format_whatsapp_number("+919876543210")
    assert clean_num == "whatsapp:+919876543210"

    already_prefixed = format_whatsapp_number("whatsapp:+919876543210")
    assert already_prefixed == "whatsapp:+919876543210"

    res = send_whatsapp_message("+919876543210", "Hello Test")
    assert res.get("mock") is True
    assert "MOCK_WA_" in res.get("sid", "")
    assert res.get("to") == "whatsapp:+919876543210"
    assert res.get("body") == "Hello Test"


# ------------------------------------------------------------------------------
# Unit Tests: Guardian MCP Tools & Handler Dispatch
# ------------------------------------------------------------------------------
def test_guardian_mcp_tools():
    """Verify request_approval, get_approval_status, notify_guardian, and get_guard_status."""
    hh_id = "HH#kamala"

    # 1. request_approval
    req_res = guardian_tools.request_approval(
        household_id=hh_id,
        action_type="MEDICINE_REORDER",
        payee_name="Apollo Pharmacy",
        amount=3800.0,
        reason="Monthly BP tablets",
    )
    assert req_res["success"] is True
    assert req_res["status"] == "PENDING"
    assert req_res["approval_id"].startswith("APR_")
    assert req_res["cooling_off_seconds"] == 20
    app_id = req_res["approval_id"]

    # 2. get_approval_status (initially PENDING)
    status_res = guardian_tools.get_approval_status(household_id=hh_id, approval_id=app_id)
    assert status_res["status"] == "PENDING"
    assert status_res["amount"] == 3800.0

    # 3. notify_guardian with HIGH severity (includes 1930 helpline and FREEZE)
    high_alert = guardian_tools.notify_guardian(
        household_id=hh_id,
        severity="HIGH",
        message="Suspicious caller claiming to be Mumbai Customs",
    )
    assert high_alert["success"] is True
    assert high_alert["severity"] == "HIGH"

    # 4. get_guard_status
    guard_status = guardian_tools.get_guard_status(household_id=hh_id)
    assert guard_status["success"] is True
    assert guard_status["frozen"] is False
    assert guard_status["auto_limit"] == 2000.0
    assert guard_status["max_with_approval"] == 25000.0
    assert guard_status["active_approvals_count"] >= 1


def test_guardian_handler_dispatch():
    """Verify Guardian MCP handler dispatches tools correctly."""
    hh_id = "HH#kamala"

    res = guardian_handler.handle_tool_call(
        "request_approval",
        {
            "household_id": hh_id,
            "action_type": "PAYMENT",
            "payee_name": "Apollo Pharmacy",
            "amount": 2500.0,
            "reason": "Test",
        },
    )
    assert res["success"] is True
    assert res["status"] == "PENDING"

    app_id = res["approval_id"]
    status_res = guardian_handler.handle_tool_call(
        "get_approval_status",
        {"household_id": hh_id, "approval_id": app_id},
    )
    assert status_res["status"] == "PENDING"


# ------------------------------------------------------------------------------
# Scenario 2: Full Approval & Cooling-Off Lifecycle (₹3,800 Medicine Reorder)
# ------------------------------------------------------------------------------
def test_scenario_2_approval_cooling_off_lifecycle():
    """Verify full approval and cooling-off state machine with Cedar evaluation:

    1. Amount = ₹3,800 (exceeds auto_limit ₹2,000, under ₹25,000 max).
    2. request_approval creates PENDING approval.
    3. Interceptor computes approval_valid=False -> Cedar DENY.
    4. Guardian replies '1' via WhatsApp -> status=APPROVED, cooling-off active (20s).
    5. During cooling-off (t + 5s) -> status=APPROVED_COOLING_OFF -> Cedar DENY.
    6. After cooling-off (t + 25s) -> status=READY -> approval_valid=True -> Cedar ALLOW.
    7. Errand reorder_medicine executes successfully and marks assessment consumed.
    """
    hh_id = "HH#kamala"
    now_dt = datetime.now(timezone.utc)
    now_epoch = int(now_dt.timestamp())

    # Step 1: Request approval
    req_res = guardian_tools.request_approval(
        household_id=hh_id,
        action_type="MEDICINE_REORDER",
        payee_name="Apollo Pharmacy",
        amount=3800.0,
        reason="Monthly BP tablets",
    )
    app_id = req_res["approval_id"]
    assert req_res["status"] == "PENDING"

    # Step 2: Interceptor check before approval -> Cedar DENY
    input_args = {
        "payee_id": "Apollo Pharmacy",
        "amount": 3800.0,
        "approval_id": app_id,
        "assessment_id": "ass_valid_phase3",
    }
    facts_pending = dict(input_args)
    facts_pending.update(compute_security_facts(hh_id, input_args, now=now_dt))
    assert facts_pending["approval_valid"] is False
    allowed, decision, _ = evaluate_policy(
        arguments=facts_pending,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed is False
    assert decision == "DENY"

    # Step 3: Guardian approves via WhatsApp by sending "1"
    webhook_reply = process_whatsapp_message(
        from_number="+919876543210",
        body="1",
        household_id=hh_id,
    )
    assert "Approval Confirmed" in webhook_reply
    assert "Cooling-off period active" in webhook_reply

    # Step 4: Check during cooling-off delay (t + 5 seconds)
    t_during = now_dt + timedelta(seconds=5)
    live_status_cooling = guardian_tools.get_approval_status(household_id=hh_id, approval_id=app_id)
    assert live_status_cooling["status"] == "APPROVED_COOLING_OFF"
    assert live_status_cooling["seconds_remaining"] > 0

    facts_during = dict(input_args)
    facts_during.update(compute_security_facts(hh_id, input_args, now=t_during))
    assert facts_during["approval_valid"] is False
    allowed_during, decision_during, _ = evaluate_policy(
        arguments=facts_during,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed_during is False
    assert decision_during == "DENY"

    # Step 5: Check after cooling-off elapses (t + 25 seconds)
    t_after = now_dt + timedelta(seconds=25)
    facts_after = dict(input_args)
    facts_after.update(compute_security_facts(hh_id, input_args, now=t_after))
    assert facts_after["approval_valid"] is True
    assert facts_after["assessment_valid"] is True
    assert facts_after["scam_flag"] is False
    assert facts_after["frozen"] is False

    allowed_after, decision_after, _ = evaluate_policy(
        arguments=facts_after,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed_after is True
    assert decision_after == "ALLOW"

    # Step 6: Execute reorder errand
    reorder_res = errand_tools.reorder_medicine(
        household_id=hh_id,
        medicine_ids=["MED#METFORMIN_500", "MED#AMLODIPINE_5"],
        pharmacy_name="Apollo Pharmacy",
        amount=3800.0,
        idem_key="IDEM_TEST_MED_3800",
        assessment_id="ass_valid_phase3",
        approval_id=app_id,
    )
    assert reorder_res["success"] is True
    assert reorder_res["total_amount"] == 3800.0
    assert "receipt_id" in reorder_res

    # Check assessment was marked used
    assess_item = get_item(hh_id, "ASSESS#ass_valid_phase3")
    assert assess_item["used"] is True


# ------------------------------------------------------------------------------
# Scenario 3: Guardian Declines (₹3,800)
# ------------------------------------------------------------------------------
def test_scenario_3_guardian_declined():
    """Verify that when Guardian replies '2' or 'DECLINE', status is DECLINED and Cedar denies."""
    hh_id = "HH#kamala"
    now_dt = datetime.now(timezone.utc)

    # 1. Request approval
    req_res = guardian_tools.request_approval(
        household_id=hh_id,
        action_type="MEDICINE_REORDER",
        payee_name="Apollo Pharmacy",
        amount=3800.0,
        reason="Monthly BP tablets",
    )
    app_id = req_res["approval_id"]

    # 2. Guardian declines
    reply = process_whatsapp_message(
        from_number="+919876543210",
        body="2",
        household_id=hh_id,
    )
    assert "Request Declined" in reply

    # 3. Check status is DECLINED
    status_res = guardian_tools.get_approval_status(household_id=hh_id, approval_id=app_id)
    assert status_res["status"] == "DECLINED"

    # 4. Interceptor & Cedar check
    facts = compute_security_facts(
        hh_id,
        {
            "payee_id": "Apollo Pharmacy",
            "amount": 3800.0,
            "approval_id": app_id,
            "assessment_id": "ass_valid_phase3",
        },
        now=now_dt + timedelta(seconds=30),
    )
    assert facts["approval_valid"] is False
    allowed, decision, _ = evaluate_policy(
        arguments=facts,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# Scenario 4: Approval Past TTL Expiration
# ------------------------------------------------------------------------------
def test_scenario_4_approval_ttl_expired():
    """Verify that an approval older than TTL is EXPIRED and Cedar denies."""
    hh_id = "HH#kamala"
    now_dt = datetime.now(timezone.utc)
    now_epoch = int(now_dt.timestamp())

    # Create an approval that was approved in the past but TTL has expired
    past_approved_at = now_epoch - 8000
    expired_ttl = now_epoch - 100
    app_id = "APR_EXPIRED_01"

    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.APPROVAL}{app_id}",
        "approval_id": app_id,
        "household_id": hh_id,
        "status": "APPROVED",
        "action_type": "MEDICINE_REORDER",
        "payee_name": "Apollo Pharmacy",
        "amount": 3800.0,
        "created_at": past_approved_at - 10,
        "approved_at": past_approved_at,
        "executable_at": past_approved_at + 20,
        "ttl": expired_ttl,
        "cooling_off_seconds": 20,
    })

    # Check status
    status_res = guardian_tools.get_approval_status(household_id=hh_id, approval_id=app_id)
    assert status_res["status"] == "EXPIRED"

    # Interceptor check
    facts = compute_security_facts(
        hh_id,
        {
            "payee_id": "Apollo Pharmacy",
            "amount": 3800.0,
            "approval_id": app_id,
            "assessment_id": "ass_valid_phase3",
        },
        now=now_dt,
    )
    assert facts["approval_valid"] is False
    allowed, decision, _ = evaluate_policy(
        arguments=facts,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# Scenario 16: Guardian Cancels Within Cooling-Off Period
# ------------------------------------------------------------------------------
def test_scenario_16_guardian_cancels_during_cooling_off():
    """Verify that replying 'CANCEL' during cooling-off marks approval CANCELLED and blocks payment."""
    hh_id = "HH#kamala"
    now_dt = datetime.now(timezone.utc)

    # 1. Request approval
    req_res = guardian_tools.request_approval(
        household_id=hh_id,
        action_type="MEDICINE_REORDER",
        payee_name="Apollo Pharmacy",
        amount=3800.0,
        reason="Monthly BP tablets",
    )
    app_id = req_res["approval_id"]

    # 2. Guardian approves
    process_whatsapp_message("+919876543210", "1", household_id=hh_id)
    status_1 = guardian_tools.get_approval_status(household_id=hh_id, approval_id=app_id)
    assert status_1["status"] == "APPROVED_COOLING_OFF"

    # 3. Guardian cancels during cooling-off
    cancel_reply = process_whatsapp_message("+919876543210", "CANCEL", household_id=hh_id)
    assert "Approval Cancelled" in cancel_reply

    # 4. Status is now CANCELLED
    status_2 = guardian_tools.get_approval_status(household_id=hh_id, approval_id=app_id)
    assert status_2["status"] == "CANCELLED"

    # 5. Check even after cooling-off period would have passed (t + 30s)
    facts = compute_security_facts(
        hh_id,
        {
            "payee_id": "Apollo Pharmacy",
            "amount": 3800.0,
            "approval_id": app_id,
            "assessment_id": "ass_valid_phase3",
        },
        now=now_dt + timedelta(seconds=30),
    )
    assert facts["approval_valid"] is False
    allowed, decision, _ = evaluate_policy(
        arguments=facts,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed is False
    assert decision == "DENY"


# ------------------------------------------------------------------------------
# Freeze / Unfreeze Webhook Behavior & Scoped Cedar Policies
# ------------------------------------------------------------------------------
def test_freeze_unfreeze_and_scoped_cedar_policies():
    """Verify that FREEZE blocks money actions but permits emergency alerts and read-only tools."""
    hh_id = "HH#kamala"

    # 1. Trigger FREEZE
    freeze_reply = process_whatsapp_message("+919876543210", "FREEZE", household_id=hh_id)
    assert "EMERGENCY FREEZE ACTIVATED" in freeze_reply

    pay_args = {
        "payee_id": "BESCOM",
        "bill_id": "BILL_BESCOM_NOV",
        "amount": 1240.0,
        "assessment_id": "ass_valid_phase3",
    }
    facts_frozen = dict(pay_args)
    facts_frozen.update(compute_security_facts(hh_id, pay_args))
    facts_frozen["hour_ist"] = 14
    assert facts_frozen["frozen"] is True

    # Money action (pay_bill) MUST be denied when frozen
    allowed_pay, dec_pay, _ = evaluate_policy(
        arguments=facts_frozen,
        tool_name="Errands___pay_bill",
        household_id="kamala",
    )
    assert allowed_pay is False
    assert dec_pay == "DENY"

    # Guardian notification (notify_guardian) MUST be allowed even when frozen!
    allowed_notify, dec_notify, _ = evaluate_policy(
        arguments=facts_frozen,
        tool_name="Guardian___notify_guardian",
        household_id="kamala",
    )
    assert allowed_notify is True
    assert dec_notify == "ALLOW"

    # Read-only profile query MUST be allowed even when frozen
    allowed_profile, dec_profile, _ = evaluate_policy(
        arguments=facts_frozen,
        tool_name="Errands___get_profile",
        household_id="kamala",
    )
    assert allowed_profile is True
    assert dec_profile == "ALLOW"

    # 2. Trigger UNFREEZE
    unfreeze_reply = process_whatsapp_message("+919876543210", "UNFREEZE", household_id=hh_id)
    assert "EMERGENCY FREEZE LIFTED" in unfreeze_reply

    facts_unfrozen = dict(pay_args)
    facts_unfrozen.update(compute_security_facts(hh_id, pay_args))
    facts_unfrozen["hour_ist"] = 14
    facts_unfrozen["payments_last_hour"] = 0
    assert facts_unfrozen["frozen"] is False

    # Money action is now allowed under normal auto-pay rules
    allowed_unfrozen_pay, dec_unfrozen_pay, _ = evaluate_policy(
        arguments=facts_unfrozen,
        tool_name="Errands___pay_bill",
        household_id="kamala",
    )
    assert allowed_unfrozen_pay is True
    assert dec_unfrozen_pay == "ALLOW"


# ------------------------------------------------------------------------------
# Inbound Webhook Lambda Handler Tests (TwiML & Form-Urlencoded Parsing)
# ------------------------------------------------------------------------------
def test_webhook_lambda_handler_url_encoded():
    """Verify webhook lambda_handler processes form-urlencoded body and returns valid TwiML XML."""
    event = {
        "headers": {
            "content-type": "application/x-www-form-urlencoded",
        },
        "body": "From=whatsapp%3A%2B919876543210&Body=STATUS",
    }
    res = webhook_lambda_handler(event)
    assert res["statusCode"] == 200
    assert res["headers"]["Content-Type"] == "application/xml"
    assert "<?xml version=" in res["body"]
    assert "<Response>" in res["body"]
    assert "<Message>" in res["body"]
    assert "BETA Status" in res["body"]


def test_webhook_lambda_handler_base64_encoded():
    """Verify webhook lambda_handler processes base64-encoded body correctly."""
    raw_payload = "From=whatsapp:+919876543210&Body=UNFREEZE"
    b64_payload = base64.b64encode(raw_payload.encode("utf-8")).decode("ascii")

    event = {
        "isBase64Encoded": True,
        "headers": {
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "body": b64_payload,
    }
    res = webhook_lambda_handler(event)
    assert res["statusCode"] == 200
    assert "EMERGENCY FREEZE LIFTED" in res["body"]
