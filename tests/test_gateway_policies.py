#!/usr/bin/env python3
"""
test_gateway_policies.py - Automated MCP Gateway & Cedar Policy Integration Test Suite
Track 1: Make MCP Matter

Validates the AWS AgentCore Gateway MCP endpoint, Request Interceptor sanitization,
Cedar Policy Engine Allow/Deny decisions, and Response Interceptor tool hiding.
"""

import pytest
from tests.mcp_client import MCPGatewayClient


@pytest.fixture
def client():
    """Initializes a fresh client with clean mock state."""
    c = MCPGatewayClient(force_mock=True)
    c.mock_transport.reset_state()
    return c


def test_r2_happy_path_bill_pay(client):
    """
    TC-05: R2 Routine Bill Payment
    Errands___pay_bill for ₹1,240 to BILLER#bescom with valid assessment_id and idem_key.
    Expected: Cedar ALLOW + valid receipt generated.
    """
    assessment_id = client.mock_transport.create_assessment()
    idem_key = "IDEM#2026-bescom-1240-01"

    response = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 1240,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
        }
    )

    assert response.get("decision") == "ALLOW", f"Expected ALLOW but got: {response}"
    assert response.get("tier") == "R2"
    assert response.get("status") == "PAID"
    assert "receipt_id" in response
    assert response["receipt_id"].startswith("REC#")


def test_r3_over_limit_denied_without_approval(client):
    """
    TC-06: R3 Over Auto-Approve Limit
    Errands___pay_bill for ₹3,800 (MED#telma40) without guardian approval.
    Expected: Cedar DENY (threshold of ₹2,000 exceeded).
    """
    assessment_id = client.mock_transport.create_assessment()
    idem_key = "IDEM#2026-apollo-3800-01"

    response = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "PAYEE#apollo",
            "amount": 3800,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
        }
    )

    assert response.get("decision") == "DENY", f"Expected DENY but got: {response}"
    assert response.get("tier") == "R3_APPROVAL_REQUIRED"
    assert "guardian approval required" in response.get("reason", "").lower()


def test_direct_gateway_bypass_50k(client):
    """
    TC-06 / Scenario 12: Direct Gateway Call Exceeding Hard Cap
    Direct gateway call attempting ₹50,000 transfer.
    Expected: Cedar DENY by hard ceiling rule (₹25,000 cap).
    """
    assessment_id = client.mock_transport.create_assessment()
    idem_key = "IDEM#bypass-attempt-50k"

    response = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 50000,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
        }
    )

    assert response.get("decision") == "DENY", f"Expected DENY but got: {response}"
    assert response.get("tier") == "HARD_CAP_EXCEEDED"
    assert "exceeds hard cap" in response.get("reason", "").lower()


def test_interceptor_strips_spoofed_security_fields(client):
    """
    TC-14 / Scenario 15: Interceptor Spoofing Defense
    Client injects spoofed security flags (approval_valid: true, frozen: false, payee_known: true)
    on a ₹15,000 payment.
    Expected: Request Interceptor overwrites with authoritative state (no approval in DynamoDB) -> Cedar DENY.
    """
    assessment_id = client.mock_transport.create_assessment()
    idem_key = "IDEM#spoof-attack-15k"

    response = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#unknown_fraud",
            "amount": 15000,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
            # Injected spoof flags attempting to bypass Cedar
            "approval_valid": True,
            "frozen": False,
            "payee_known": True,
            "scam_flag": False,
        }
    )

    assert response.get("decision") == "DENY", f"Expected DENY after spoof stripping but got: {response}"
    assert response.get("tier") == "R3_APPROVAL_REQUIRED"


def test_idempotency_duplicate_idem_key(client):
    """
    TC-15 / Scenarios 5 & 13: Idempotency Key Replay Protection
    Fire two Errands___pay_bill calls with identical idem_key.
    Expected: Exactly 1 payment execution; both calls return identical receipt_id with cached=True on 2nd.
    """
    assessment_id = client.mock_transport.create_assessment()
    idem_key = "IDEM#repeat-test-key-999"

    # First call -> Payment executes
    resp1 = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 1240,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
        }
    )
    assert resp1.get("decision") == "ALLOW"
    receipt_1 = resp1.get("receipt_id")
    assert receipt_1 is not None

    # Second call -> Replays identical key
    resp2 = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 1240,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
        }
    )
    assert resp2.get("decision") == "ALLOW"
    assert resp2.get("cached") is True
    assert resp2.get("receipt_id") == receipt_1, "Duplicate key must return identical receipt_id"


def test_missing_or_reused_assessment_id(client):
    """
    TC-16 & TC-17: Assessment ID Validation & Single-Use Enforcement
    Part 1: Missing assessment_id -> DENY.
    Part 2: Legitimate payment consumes assessment_id -> Replay call with same assessment_id -> DENY.
    """
    idem_key_1 = "IDEM#assess-check-1"

    # Part 1: Missing assessment_id
    resp_missing = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 1240,
            "assessment_id": None,
            "idem_key": idem_key_1,
        }
    )
    assert resp_missing.get("decision") == "DENY"
    assert resp_missing.get("tier") == "ASSESSMENT_REQUIRED"

    # Part 2: First legitimate use consumes the assessment
    assessment_id = client.mock_transport.create_assessment()
    resp_legit = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 1240,
            "assessment_id": assessment_id,
            "idem_key": idem_key_1,
        }
    )
    assert resp_legit.get("decision") == "ALLOW"

    # Second call with DIFFERENT idem_key but REUSED assessment_id
    idem_key_2 = "IDEM#assess-check-2"
    resp_reused = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 500,
            "assessment_id": assessment_id,
            "idem_key": idem_key_2,
        }
    )
    assert resp_reused.get("decision") == "DENY"
    assert resp_reused.get("tier") == "ASSESSMENT_REUSED"
    assert "already consumed" in resp_reused.get("reason", "").lower()


def test_scam_keyword_in_note_triggers_r4_block(client):
    """
    TC-12 / Scenario 19: R4 Scam & Coercion Defense
    Call Errands___pay_bill for ₹500 to known biller with 'CBI digital arrest parcel OTP' in note.
    Expected: Interceptor flags scam_flag=True -> Cedar DENY with R4 helpline advice.
    """
    assessment_id = client.mock_transport.create_assessment()
    idem_key = "IDEM#scam-test-01"

    response = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 500,
            "assessment_id": assessment_id,
            "idem_key": idem_key,
            "note": "CBI digital arrest parcel OTP - transfer required immediately",
        }
    )

    assert response.get("decision") == "DENY", f"Expected R4 DENY but got: {response}"
    assert response.get("tier") == "R4_SCAM"
    assert "1930" in response.get("reason", "")


def test_freeze_blocks_calls_and_hides_money_tools(client):
    """
    TC-18 & TC-19: Emergency Freeze Kill Switch & Response Interceptor Tool Masking
    1. Verify money tools are visible when active.
    2. Activate Freeze.
    3. Verify Errands___pay_bill is immediately DENY with FREEZE_BLOCKED.
    4. Verify tools/list filters out Errands___pay_bill and Errands___reorder_medicine,
       leaving safe read tools like Errands___get_due_bills intact.
    """
    # 1. Active state verification
    tools_before = client.list_tools()
    tool_names_before = [t["name"] for t in tools_before]
    assert "Errands___pay_bill" in tool_names_before
    assert "Errands___reorder_medicine" in tool_names_before
    assert "Errands___get_due_bills" in tool_names_before

    # 2. Trigger Freeze
    freeze_resp = client.call_tool("Guardian___freeze_account", {"household_id": "HH#kamala"})
    assert freeze_resp.get("status") == "FROZEN"

    # 3. Pay bill call must be blocked by Cedar
    assessment_id = client.mock_transport.create_assessment()
    pay_resp = client.call_tool(
        "Errands___pay_bill",
        {
            "household_id": "HH#kamala",
            "biller": "BILLER#bescom",
            "amount": 1240,
            "assessment_id": assessment_id,
            "idem_key": "IDEM#freeze-attempt-01",
        }
    )
    assert pay_resp.get("decision") == "DENY"
    assert pay_resp.get("tier") == "FREEZE_BLOCKED"

    # 4. Response Interceptor masks payment tools on tools/list
    tools_after = client.list_tools()
    tool_names_after = [t["name"] for t in tools_after]

    assert "Errands___pay_bill" not in tool_names_after, "Errands___pay_bill must be hidden when frozen"
    assert "Errands___reorder_medicine" not in tool_names_after, "Errands___reorder_medicine must be hidden when frozen"
    assert "Errands___get_due_bills" in tool_names_after, "Read tools must remain visible when frozen"
