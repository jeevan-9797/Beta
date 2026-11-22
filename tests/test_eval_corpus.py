"""Validation tests for Scam Shield evaluation dataset and contracts."""

import json
from pathlib import Path
import pytest
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


def test_eval_corpus_structure():
    corpus_path = Path("data/scam_corpus_eval.json")
    assert corpus_path.exists(), "scam_corpus_eval.json must exist"

    with open(corpus_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) == 80, f"Expected 80 cases, got {len(data)}"

    scam_cases = [item for item in data if item["id"].startswith("scam_")]
    benign_cases = [item for item in data if item["id"].startswith("benign_")]

    assert len(scam_cases) == 40, f"Expected 40 scam cases, got {len(scam_cases)}"
    assert len(benign_cases) == 40, f"Expected 40 benign cases, got {len(benign_cases)}"

    required_keys = {
        "id",
        "text",
        "intent",
        "amount",
        "expected_tier",
        "expected_risk_level",
        "signals",
    }

    valid_tiers = {tier.value for tier in RiskTier}
    valid_risk_levels = {level.value for level in RiskLevel}

    for item in data:
        item_id = item.get("id")
        assert required_keys.issubset(item.keys()), f"Item {item_id} missing keys: {required_keys - set(item.keys())}"
        assert isinstance(item["text"], str) and len(item["text"]) > 10, f"{item_id} has invalid text"
        assert isinstance(item["intent"], str) and item["intent"], f"{item_id} has invalid intent"
        assert item["amount"] is None or isinstance(item["amount"], (int, float)), f"{item_id} invalid amount"
        assert item["expected_tier"] in valid_tiers, f"{item_id} has invalid expected_tier: {item['expected_tier']}"
        assert item["expected_risk_level"] in valid_risk_levels, f"{item_id} has invalid risk level: {item['expected_risk_level']}"
        assert isinstance(item["signals"], list) and len(item["signals"]) > 0, f"{item_id} has empty signals"

        # Validate that AssessRiskInput can be instantiated from this item
        risk_input = AssessRiskInput(
            household_id="test_hh_101",
            snippet=item["text"],
            intent=item["intent"],
            amount=item["amount"],
        )
        assert risk_input.snippet == item["text"]
