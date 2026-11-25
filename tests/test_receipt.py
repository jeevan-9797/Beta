"""Tests for Bedrock Plain-Language Receipt Generator with 1-sentence 'Why BETA did this' explanation."""

from src.bedrock.receipt_generator import BedrockReceiptGenerator, generate_receipt
from src.shared.contracts import ReceiptInput, RiskTier


def test_receipt_fallback_routine_utility_with_why_reason():
    # Official Demo Seed: Journey A (Tier R2) - BESCOM electricity bill for ₹1,240
    gen = BedrockReceiptGenerator()
    inp = ReceiptInput(
        household_id="hh_blr_4012",
        action_type="utility_payment",
        payee="BESCOM",
        amount=1240.0,
        tier=RiskTier.R2,
        decision="EXECUTED",
        reason_facts={"reference_id": "TXN_771892", "due_date": "28 Nov"},
    )
    out = gen._generate_fallback(inp)

    # Spoken summary must include elder honorific and why BETA did this
    assert "Uncleji" in out.spoken_summary
    assert "BESCOM" in out.spoken_summary
    assert "1,240.00" in out.spoken_summary
    assert "kyunki" in out.spoken_summary.lower()

    # WhatsApp summary must have structured reason bullet
    assert "TXN_771892" in out.whatsapp_summary
    assert "EXECUTED" in out.whatsapp_summary
    assert "Why BETA did this" in out.whatsapp_summary


def test_receipt_fallback_blocked_scam_with_why_reason():
    # Official Demo Seed: Journey C (Tier R4) - Fake CBI digital arrest for ₹2,00,000
    gen = BedrockReceiptGenerator()
    inp = ReceiptInput(
        household_id="hh_delhi_9921",
        action_type="scam_prevention",
        payee="Fake CBI Officer",
        amount=200000.0,
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


def test_receipt_fallback_guardian_r3_cooling_off():
    # Official Demo Seed: Journey B (Tier R3) - Medicine reorder for ₹3,800
    gen = BedrockReceiptGenerator()
    inp = ReceiptInput(
        household_id="hh_mumbai_881",
        action_type="medicine_reorder_above_limit",
        payee="Apollo Pharmacy",
        amount=3800.0,
        tier=RiskTier.R3,
        decision="HELD_FOR_APPROVAL",
        reason_facts={"cooling_off_seconds": 20},
    )
    out = gen._generate_fallback(inp)

    assert "Uncleji" in out.spoken_summary
    assert "₹2,000" in out.spoken_summary
    assert "approval request" in out.spoken_summary
    assert "3,800.00" in out.whatsapp_summary
    assert "Guardian Approval Required" in out.whatsapp_summary


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
