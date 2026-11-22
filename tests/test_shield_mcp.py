"""Tests for shield-mcp server tools, DynamoDB single-table schema, and Lambda handler."""

import pytest
from src.shield_mcp.server import (
    clear_mock_db,
    get_assessment_record,
    lambda_handler,
    mark_assessment_used,
    shield_assess_risk,
    shield_explain_scam,
)
from src.shield_mcp.keywords import get_shield_keywords, get_whitelisted_billers


@pytest.fixture(autouse=True)
def clean_db():
    clear_mock_db()
    yield
    clear_mock_db()


def test_keywords_export():
    keywords = get_shield_keywords()
    assert "digital_arrest" in keywords
    assert "electricity_scam" in keywords
    assert "cbi" in keywords["digital_arrest"]

    billers = get_whitelisted_billers()
    assert "bescom" in billers
    assert "apollo pharmacy" in billers


def test_shield_assess_risk_scam_digital_arrest():
    snippet = "Beta, CBI officer bol raha hai Skype video call pe digital arrest kiya hai aur ₹2,50,000 mang raha hai."
    res = shield_assess_risk(
        household_id="hh_delhi_101",
        snippet=snippet,
        intent="digital_arrest",
        amount=250000.0,
    )
    assert res["tier"] == "R4"
    assert res["risk_level"] == "HIGH"
    assert res["recommended_action"] == "BLOCK_AND_ALERT"
    assert "assessment_id" in res
    assert res["expires_at"] > 0

    # Verify DynamoDB single-table record
    record = get_assessment_record("hh_delhi_101", res["assessment_id"])
    assert record is not None
    assert record["PK"] == "HH#hh_delhi_101"
    assert record["SK"] == f"ASSESS#{res['assessment_id']}"
    assert record["tier"] == "R4"
    assert record["used"] is False


def test_shield_assess_risk_guardian_gate_r2_small_utility():
    snippet = "Beta, is mahine ka BESCOM bijli bill ₹1,450 clear kar do."
    res = shield_assess_risk(
        household_id="hh_blr_4012",
        snippet=snippet,
        intent="pay_utility_bill",
        amount=1450.0,
    )
    # Under Guardian Gate: known payee <= 2000 is Tier R2
    assert res["tier"] == "R2"
    assert res["risk_level"] == "LOW"
    assert res["recommended_action"] == "ALLOW"
    assert res["assessment_id"].startswith("eval_")

    # Verify DynamoDB record
    record = get_assessment_record("hh_blr_4012", res["assessment_id"])
    assert record is not None
    assert record["PK"] == "HH#hh_blr_4012"
    assert record["tier"] == "R2"


def test_shield_assess_risk_guardian_gate_r3_high_amount():
    snippet = "Beta, is mahine ka Tata Power bill ₹4,500 clear kar do."
    res = shield_assess_risk(
        household_id="hh_mumbai_881",
        snippet=snippet,
        intent="pay_utility_bill",
        amount=4500.0,
    )
    # Under Guardian Gate: amount > 2000 requires Guardian WhatsApp approval (R3)
    assert res["tier"] == "R3"
    assert res["risk_level"] == "MEDIUM"
    assert res["recommended_action"] == "REQUIRE_GUARDIAN_APPROVAL"


def test_shield_assess_risk_guardian_gate_r1_errand():
    snippet = "Dr. Sharma ke sath kal subah 11 baje ka appointment book kar do."
    res = shield_assess_risk(
        household_id="hh_delhi_101",
        snippet=snippet,
        intent="book_doctor_appointment",
        amount=None,
    )
    # Under Guardian Gate: low reversible errand is Tier R1
    assert res["tier"] == "R1"
    assert res["risk_level"] == "LOW"
    assert res["recommended_action"] == "ALLOW"


def test_shield_assess_risk_guardian_gate_r0_read():
    snippet = "Beta, bijli ka bill status check karo aur amount read karo."
    res = shield_assess_risk(
        household_id="hh_delhi_101",
        snippet=snippet,
        intent="check_bill_status",
        amount=None,
    )
    # Under Guardian Gate: read-only inquiry is Tier R0
    assert res["tier"] == "R0"
    assert res["risk_level"] == "LOW"
    assert res["recommended_action"] == "ALLOW"


def test_mark_assessment_used_lifecycle():
    snippet = "Beta, Apollo se Amlodipine 5mg reorder kar do ₹380."
    res = shield_assess_risk(
        household_id="hh_blr_4012",
        snippet=snippet,
        intent="reorder_medicine",
        amount=380.0,
    )
    token = res["assessment_id"]

    # First use: returns True
    assert mark_assessment_used("hh_blr_4012", token) is True

    # Re-use attempt: returns False (token replay prevention)
    assert mark_assessment_used("hh_blr_4012", token) is False


def test_shield_explain_scam():
    res = shield_explain_scam(scam_type="digital_arrest", language="hi-en", household_id="hh_delhi_101")
    assert res["helpline"] == "1930"
    assert "CBI" in res["script_spoken"]
    assert "hh_delhi_101" in res["guardian_alert_text"]


def test_lambda_handler_bedrock_agent_format():
    event = {
        "messageVersion": "1.0",
        "actionGroup": "shield-mcp",
        "apiPath": "/assess_risk",
        "httpMethod": "POST",
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [
                        {"name": "household_id", "value": "hh_delhi_101"},
                        {"name": "snippet", "value": "CBI video call arrest ₹2,00,000"},
                        {"name": "intent", "value": "digital_arrest"},
                        {"name": "amount", "value": "200000"},
                    ]
                }
            }
        },
    }

    resp = lambda_handler(event, None)
    assert resp["messageVersion"] == "1.0"
    assert resp["response"]["httpStatusCode"] == 200

    import json
    body = json.loads(resp["response"]["responseBody"]["application/json"]["body"])
    assert body["tier"] == "R4"
    assert body["risk_level"] == "HIGH"
