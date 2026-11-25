"""Tests for scripts/sync_gateway_tools.py."""

import json
from pathlib import Path
import pytest
from scripts.sync_gateway_tools import (
    DEFAULT_MOCK_GATEWAY_TOOLS,
    build_tool_name_mappings,
    generate_live_system_prompt,
    generate_live_tool_descriptions,
    main,
)


def test_build_tool_name_mappings():
    mappings = build_tool_name_mappings(DEFAULT_MOCK_GATEWAY_TOOLS)

    assert mappings["shield.assess_risk"] == "Shield___assess_risk"
    assert mappings["shield.explain_scam"] == "Shield___explain_scam"
    assert mappings["errands.pay_bill"] == "Errands___pay_bill"
    assert mappings["errands.book_appointment"] == "Errands___book_appointment"
    assert mappings["guardian.request_approval"] == "Guardian___request_approval"
    assert mappings["guardian.notify_alert"] == "Guardian___notify_alert"


def test_generate_live_system_prompt_substitution():
    sample_prompt = (
        "Call `shield.assess_risk(...)` before calling `errands.pay_bill(...)`. "
        "If R3, call `guardian.request_approval`."
    )
    mappings = {
        "shield.assess_risk": "Shield___assess_risk",
        "errands.pay_bill": "Errands___pay_bill",
        "guardian.request_approval": "Guardian___request_approval",
    }
    live_prompt = generate_live_system_prompt(sample_prompt, mappings)

    assert "Shield___assess_risk" in live_prompt
    assert "Errands___pay_bill" in live_prompt
    assert "Guardian___request_approval" in live_prompt
    assert "shield.assess_risk" not in live_prompt
    assert "errands.pay_bill" not in live_prompt


def test_generate_live_tool_descriptions_post_tool_speech():
    base_config = {
        "agent_name": "BETA",
        "version": "1.0.0",
        "tools": [
            {"name": "errands.pay_bill", "execution_mode": "post_tool_speech"},
            {"name": "shield.assess_risk", "execution_mode": "immediate"},
        ]
    }
    mappings = {
        "errands.pay_bill": "Errands___pay_bill",
        "shield.assess_risk": "Shield___assess_risk",
    }
    live_config = generate_live_tool_descriptions(base_config, DEFAULT_MOCK_GATEWAY_TOOLS, mappings)

    pay_bill_tool = next((t for t in live_config["tools"] if "pay_bill" in t["name"]), None)
    assert pay_bill_tool is not None
    assert pay_bill_tool["name"] == "Errands___pay_bill"
    assert pay_bill_tool["execution_mode"] == "post_tool_speech"

    shield_tool = next((t for t in live_config["tools"] if "assess_risk" in t["name"]), None)
    assert shield_tool is not None
    assert shield_tool["execution_mode"] == "immediate"


def test_main_mock_run(monkeypatch):
    test_args = ["sync_gateway_tools.py", "--mock"]
    monkeypatch.setattr("sys.argv", test_args)
    exit_code = main()
    assert exit_code == 0

    # Verify generated files exist
    live_prompt_path = Path("agent_config/system_prompt_live.md")
    live_tools_path = Path("agent_config/tool_descriptions_live.json")
    assert live_prompt_path.exists()
    assert live_tools_path.exists()

    with open(live_tools_path, "r", encoding="utf-8") as f:
        tools_data = json.load(f)
    assert len(tools_data["tools"]) >= 8
