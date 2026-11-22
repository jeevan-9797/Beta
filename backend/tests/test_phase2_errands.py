"""Phase 2 Tests - Complete Errands Suite, Schema Alignment, and Google Calendar Integration.

Verifies:
- Journey A end-to-end:
    get_due_bills -> pay_bill (₹1,240 BESCOM) -> find_slots -> book_appointment (calendar event + receipt)
- Journey B trigger:
    reorder_medicine (₹3,800) denied by Cedar without approval_valid=True,
    allowed with valid approval + fresh ASSESS#<id>
- Profile, billers, payees, meds listings
- Reminder creation in Google Calendar and DynamoDB
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
)
from backend.mcp_errands import tools as errand_tools
from backend.policies.evaluator import evaluate_policy


@pytest.fixture(autouse=True)
def setup_kamala_environment():
    """Setup mock database with reconciled HH#kamala schema."""
    mock_db = reset_mock_db()
    hh_id = "HH#kamala"
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Members
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

    # 2. Rules
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
        "night_start": 23,
        "night_end": 6,
        "max_payments_per_hour": 2,
    })

    # 3. Billers: BESCOM & BWSSB
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.BILLER}BESCOM",
        "biller_id": "BESCOM",
        "name": "BESCOM Electricity",
        "bill_id": "BILL_BESCOM_NOV",
        "amount": 1240.0,
        "due_date": "28 Nov",
        "status": "PENDING",
    })
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.BILLER}BWSSB",
        "biller_id": "BWSSB",
        "name": "BWSSB Water Supply",
        "bill_id": "BILL_BWSSB_NOV",
        "amount": 450.0,
        "due_date": "30 Nov",
        "status": "PENDING",
    })

    # 4. Payee: Apollo Pharmacy
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.PAYEE}APOLLO_PHARMACY",
        "payee_id": "APOLLO_PHARMACY",
        "name": "Apollo Pharmacy",
        "category": "Pharmacy",
        "upi_id": "apollopharmacy@icici",
        "verified": True,
    })

    # 5. Meds
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.MED}METFORMIN_500MG",
        "med_id": "METFORMIN_500MG",
        "name": "Metformin 500mg",
        "dosage": "500mg",
        "refill_qty": 60,
        "unit_price": 20.0,
        "reorder_total": 1200.0,
    })
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.MED}AMLODIPINE_5MG",
        "med_id": "AMLODIPINE_5MG",
        "name": "Amlodipine 5mg",
        "dosage": "5mg",
        "refill_qty": 60,
        "unit_price": 43.333,
        "reorder_total": 2600.0,
    })

    return mock_db


def make_gateway_event(arguments: dict, tool_name: str = "pay_bill") -> dict:
    return {
        "mcp": {
            "gatewayRequest": {
                "headers": {"Content-Type": "application/json"},
                "body": {
                    "jsonrpc": "2.0",
                    "id": "req-002",
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                },
            }
        }
    }


def extract_args(interceptor_res: dict) -> dict:
    body = interceptor_res["mcp"]["transformedGatewayRequest"]["body"]
    if isinstance(body, str):
        body = json.loads(body)
    return body["params"]["arguments"]


# ------------------------------------------------------------------------------
# Test Informational Tools: get_profile, list_billers, list_payees, list_meds
# ------------------------------------------------------------------------------
def test_informational_tools():
    hh_id = "HH#kamala"

    # Profile
    profile = errand_tools.get_profile(hh_id)
    assert profile["success"] is True
    assert profile["parent"]["name"] == "Kamala"
    assert profile["guardian"]["name"] == "Arjun"
    assert profile["limits"]["auto_limit"] == 2000.0
    assert profile["frozen"] is False

    # Billers
    billers_res = errand_tools.list_billers(hh_id)
    assert billers_res["success"] is True
    assert billers_res["count"] == 2

    # Payees
    payees_res = errand_tools.list_payees(hh_id)
    assert payees_res["success"] is True
    assert payees_res["count"] == 1
    assert payees_res["payees"][0]["name"] == "Apollo Pharmacy"

    # Meds
    meds_res = errand_tools.list_meds(hh_id)
    assert meds_res["success"] is True
    assert meds_res["count"] == 2


# ------------------------------------------------------------------------------
# JOURNEY A END-TO-END:
# get_due_bills -> pay_bill (BESCOM ₹1,240) -> find_slots -> book_appointment
# ------------------------------------------------------------------------------
def test_journey_a_end_to_end():
    hh_id = "HH#kamala"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=300)).isoformat()

    # Step 1: Query due bills
    due_res = errand_tools.get_due_bills(hh_id)
    assert due_res["success"] is True
    assert due_res["count"] == 2
    bescom_bill = next((b for b in due_res["due_bills"] if b["biller_id"] == "BESCOM"), None)
    assert bescom_bill is not None
    assert bescom_bill["amount"] == 1240.0
    assert bescom_bill["status"] == "PENDING"

    # Step 2: Pay BESCOM bill with fresh assessment under ASSESS#
    assessment_id = "ASM_JOURNEY_A_001"
    put_item({
        "PK": hh_id,
        "SK": f"ASSESS#{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "tier": "R1",
        "signals": {"confusion_score": 0.05},
        "used": False,
        "created_at": now_iso,
        "expires_at": expires_iso,
    })

    pay_args = {
        "household_id": hh_id,
        "bill_id": "BILL_BESCOM_NOV",
        "payee_id": "BESCOM",
        "amount": 1240.0,
        "idem_key": "IDEM_JOURNEY_A_BESCOM",
        "assessment_id": assessment_id,
        "note": "Kamala monthly electricity payment",
    }
    event = make_gateway_event(pay_args, tool_name="Errands___pay_bill")
    intercepted = process_gateway_request(event)
    transformed = extract_args(intercepted)

    # Assert security facts computed correctly
    assert transformed["payee_known"] is True
    assert transformed["assessment_valid"] is True
    assert transformed["frozen"] is False
    assert transformed["scam_flag"] is False
    transformed["hour_ist"] = 12
    transformed["payments_last_hour"] = 0

    # Cedar authorization
    allowed, decision, diag = evaluate_policy(
        transformed,
        tool_name="Errands___pay_bill",
        household_id="kamala",
    )
    assert allowed is True
    assert decision == "ALLOW"

    # Tool execution
    pay_res = errand_tools.pay_bill(**transformed)
    assert pay_res["success"] is True
    assert pay_res["status"] == "SUCCESS"
    assert pay_res["provider"] == "MOCK_BBPS"
    assert pay_res["amount"] == 1240.0

    # Verify assessment consumed (used = True)
    asm_item = get_item(hh_id, f"ASSESS#{assessment_id}")
    assert asm_item["used"] is True

    # Verify BESCOM bill status updated to PAID
    due_res_after = errand_tools.get_due_bills(hh_id)
    assert due_res_after["count"] == 1  # Only BWSSB left

    # Step 3: Find appointment slots
    slots_res = errand_tools.find_slots(specialty="Cardiologist")
    assert slots_res["success"] is True
    assert slots_res["count"] >= 1
    slot = slots_res["slots"][0]
    assert "Dr. Rao" in slot["doctor_name"]

    # Step 4: Book clinic appointment with Google Calendar integration
    book_res = errand_tools.book_appointment(
        household_id=hh_id,
        doctor_name=slot["doctor_name"],
        slot_iso=slot["slot_iso"],
        patient_name="Kamala",
    )
    assert book_res["success"] is True
    assert book_res["status"] == "CONFIRMED"
    assert book_res["doctor_name"] == slot["doctor_name"]
    assert "calendar_event_id" in book_res
    appointment_id = book_res["appointment_id"]

    # Verify receipt saved in DynamoDB
    receipt = get_item(hh_id, f"{SKPrefix.RECEIPT}{appointment_id}")
    assert receipt is not None
    assert receipt["provider"] == "GOOGLE_CALENDAR"
    assert receipt["status"] == "CONFIRMED"


# ------------------------------------------------------------------------------
# JOURNEY B: reorder_medicine (₹3,800)
# Denied without approval_valid=True, allowed with valid approval + fresh ASSESS#
# ------------------------------------------------------------------------------
def test_journey_b_reorder_medicine_approval_workflow():
    hh_id = "HH#kamala"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=300)).isoformat()
    assessment_id = "ASM_JOURNEY_B_001"

    # Seed fresh assessment under ASSESS#
    put_item({
        "PK": hh_id,
        "SK": f"ASSESS#{assessment_id}",
        "assessment_id": assessment_id,
        "risk_level": "R1",
        "tier": "R1",
        "used": False,
        "created_at": now_iso,
        "expires_at": expires_iso,
    })

    med_args = {
        "household_id": hh_id,
        "med_ids": ["METFORMIN_500MG", "AMLODIPINE_5MG"],
        "amount": 3800.0,
        "idem_key": "IDEM_JOURNEY_B_MEDS",
        "assessment_id": assessment_id,
        "note": "Kamala monthly diabetes and BP tablets",
    }

    # ATTEMPT 1: Without Guardian Approval
    event_1 = make_gateway_event(med_args, tool_name="Errands___reorder_medicine")
    intercepted_1 = process_gateway_request(event_1)
    transformed_1 = extract_args(intercepted_1)

    assert transformed_1["approval_valid"] is False
    assert transformed_1["assessment_valid"] is True
    transformed_1["hour_ist"] = 12
    transformed_1["payments_last_hour"] = 0

    # Cedar must DENY because ₹3,800 > auto_limit ₹2,000 and approval_valid is False
    allowed_1, decision_1, _ = evaluate_policy(
        transformed_1,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed_1 is False
    assert decision_1 == "DENY"

    # ATTEMPT 2: Guardian Arjun grants approval via WhatsApp / Dashboard
    approval_id = "APP_MEDS_3800_OCT"
    past_approved_at = (now - timedelta(seconds=30)).isoformat()
    put_item({
        "PK": hh_id,
        "SK": f"{SKPrefix.APPROVAL}{approval_id}",
        "approval_id": approval_id,
        "errand_type": "MEDICINE_REORDER",
        "payee_name": "Apollo Pharmacy",
        "amount": 3800.0,
        "status": "APPROVED",
        "approved_at": past_approved_at,
        "cooling_off_seconds": 20,
        "expires_at": (now + timedelta(hours=2)).isoformat(),
    })

    med_args_approved = dict(med_args)
    med_args_approved["approval_id"] = approval_id

    event_2 = make_gateway_event(med_args_approved, tool_name="Errands___reorder_medicine")
    intercepted_2 = process_gateway_request(event_2)
    transformed_2 = extract_args(intercepted_2)

    assert transformed_2["approval_valid"] is True
    assert transformed_2["payee_known"] is True
    assert transformed_2["assessment_valid"] is True
    transformed_2["hour_ist"] = 12
    transformed_2["payments_last_hour"] = 0

    # Cedar must now ALLOW
    allowed_2, decision_2, _ = evaluate_policy(
        transformed_2,
        tool_name="Errands___reorder_medicine",
        household_id="kamala",
    )
    assert allowed_2 is True
    assert decision_2 == "ALLOW"

    # Tool executes successfully
    order_res = errand_tools.reorder_medicine(**transformed_2)
    assert order_res["success"] is True
    assert order_res["status"] == "CONFIRMED"
    assert order_res["provider"] == "MOCK_PHARMACY"
    assert order_res["amount"] == 3800.0

    # Assessment consumed (used = True)
    asm_item = get_item(hh_id, f"ASSESS#{assessment_id}")
    assert asm_item["used"] is True

    # Idempotent retry returns identical receipt
    retry_order = errand_tools.reorder_medicine(**transformed_2)
    assert retry_order["success"] is True
    assert retry_order["idempotent_replay"] is True
    assert retry_order["tx_id"] == order_res["tx_id"]


# ------------------------------------------------------------------------------
# Test Reminder Tool: set_reminder
# ------------------------------------------------------------------------------
def test_set_reminder_tool():
    hh_id = "HH#kamala"
    time_target = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    rem_res = errand_tools.set_reminder(
        household_id=hh_id,
        title="Take Metformin 500mg after lunch",
        time_iso=time_target,
    )
    assert rem_res["success"] is True
    assert rem_res["status"] == "ACTIVE"
    reminder_id = rem_res["reminder_id"]

    # Verify persisted in DynamoDB
    rem_item = get_item(hh_id, f"{SKPrefix.REMINDER}{reminder_id}")
    assert rem_item is not None
    assert rem_item["title"] == "Take Metformin 500mg after lunch"
