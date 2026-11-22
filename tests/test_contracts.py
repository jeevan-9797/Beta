"""Tests for BETA data contracts, validations, and serialization."""

import pytest
from pydantic import ValidationError
from src.shared.contracts import (
    AssessRiskInput,
    AssessRiskOutput,
    ExplainScamInput,
    ExplainScamOutput,
    ReceiptInput,
    ReceiptOutput,
    RiskLevel,
    RiskTier,
)


def test_risk_enums():
    assert [t.value for t in RiskTier] == ["R0", "R1", "R2", "R3", "R4"]
    assert [r.value for r in RiskLevel] == ["LOW", "MEDIUM", "HIGH"]


def test_assess_risk_input_valid():
    payload = {
        "household_id": "hh_delhi_101",
        "snippet": "Beta, BESCOM bijli ka bill pay kar do ₹1,450.",
        "intent": "pay_utility_bill",
        "amount": 1450.0,
    }
    obj = AssessRiskInput(**payload)
    assert obj.household_id == "hh_delhi_101"
    assert obj.amount == 1450.0

    # Test with amount=None
    obj_no_amt = AssessRiskInput(
        household_id="hh_delhi_101",
        snippet="Dr. Sharma appointment",
        intent="book_doctor_appointment",
    )
    assert obj_no_amt.amount is None


def test_assess_risk_input_missing_required():
    with pytest.raises(ValidationError):
        AssessRiskInput(household_id="hh_delhi_101")  # missing snippet and intent


def test_assess_risk_output_contract():
    output = AssessRiskOutput(
        assessment_id="sec_token_991823",
        risk_level=RiskLevel.HIGH,
        tier=RiskTier.R4,
        signals=["cbi_impersonation", "digital_arrest"],
        recommended_action="BLOCK_AND_ALERT",
        expires_at=1774390300,
    )
    assert output.risk_level == RiskLevel.HIGH
    assert output.tier == RiskTier.R4
    assert len(output.signals) == 2
    assert output.recommended_action == "BLOCK_AND_ALERT"


def test_explain_scam_contract():
    inp = ExplainScamInput(scam_type="digital_arrest", language="hi-en")
    assert inp.scam_type == "digital_arrest"
    assert inp.language == "hi-en"

    out = ExplainScamOutput(
        script_spoken="Uncleji, CBI kabhi video call pe arrest nahi karti. Yeh fraud hai.",
        helpline="1930",
        guardian_alert_text="Alert: Uncleji received fake CBI call. Blocked.",
    )
    assert out.helpline == "1930"
    assert "CBI" in out.script_spoken


def test_receipt_contracts():
    receipt_in = ReceiptInput(
        household_id="hh_blr_4012",
        action_type="utility_payment",
        payee="BESCOM",
        amount=1450.0,
        tier=RiskTier.R1,
        decision="EXECUTED",
        reason_facts={"consumer_no": "90812314", "reference_id": "TXN_991204"},
    )
    assert receipt_in.payee == "BESCOM"
    assert receipt_in.tier == RiskTier.R1

    receipt_out = ReceiptOutput(
        spoken_summary="Uncleji, aapka ₹1,450 ka BESCOM bill successfully pay ho gaya.",
        whatsapp_summary="Receipt: BESCOM ₹1,450 paid successfully.",
    )
    assert "1,450" in receipt_out.spoken_summary
