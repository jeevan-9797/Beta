"""Tests for Bedrock Plain-Language Receipt Generator with 1-sentence 'Why BETA did this' explanation."""

from src.bedrock.receipt_generator import BedrockReceiptGenerator, generate_receipt
from src.shared.contracts import ReceiptInput, RiskTier


def test_receipt_fallback_routine_utility_with_why_reason():
    gen = BedrockReceiptGenerator()
    inp = ReceiptInput(
        household_id="hh_blr_4012",
        action_type="utility_payment",
        payee="BESCOM",
        amount=1450.0,
        tier=RiskTier.R2,
        decision="EXECUTED",
        reason_facts={"reference_id": "TXN_771892"},
    )
    out = gen._generate_fallback(inp)

    # Spoken summary must include elder honorific and why BETA did this
    assert "Uncleji" in out.spoken_summary
    assert "BESCOM" in out.spoken_summary
    assert "1,450.00" in out.spoken_summary
    assert "kyunki" in out.spoken_summary.lower()

    # WhatsApp summary must have structured reason bullet
    assert "TXN_771892" in out.whatsapp_summary
    assert "EXECUTED" in out.whatsapp_summary
    assert "Why BETA did this" in out.whatsapp_summary


def test_receipt_fallback_blocked_scam_with_why_reason():
    gen = BedrockReceiptGenerator()
    inp = ReceiptInput(
        household_id="hh_delhi_9921",
        action_type="scam_prevention",
        payee="Fake CBI Officer",
        amount=250000.0,
        tier=RiskTier.R4,
        decision="BLOCKED",
        reason_facts={"threat": "digital_arrest"},
    )
    out = gen._generate_fallback(inp)

    assert "rok diya hai" in out.spoken_summary
    assert "kyunki" in out.spoken_summary.lower()
    assert "1930" in out.whatsapp_summary
    assert "BLOCKED" in out.whatsapp_summary
    assert "Why BETA did this" in out.whatsapp_summary


def test_generate_receipt_wrapper():
    res = generate_receipt(
        household_id="hh_delhi_101",
        action_type="medicine_reorder",
        payee="Apollo Pharmacy",
        amount=380.0,
        tier=RiskTier.R2,
        decision="EXECUTED",
        reason_facts={"rx": "Amlodipine 5mg"},
    )
    assert "spoken_summary" in res
    assert "whatsapp_summary" in res
    assert "Why BETA did this" in res["whatsapp_summary"]
