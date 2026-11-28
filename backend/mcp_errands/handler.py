"""MCP Errands server request handler."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from backend.mcp_errands import tools

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "tool_schema.json"


def get_tool_definitions() -> Dict[str, Any]:
    """Load and return MCP tool definitions schema."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch tool execution to appropriate Python function."""
    logger.info(f"Errands MCP executing tool: {tool_name} with arguments {arguments}")

    # Normalize tool name (handle AgentCore format Errands___pay_bill or pay_bill)
    clean_name = tool_name
    if "___" in clean_name:
        clean_name = clean_name.split("___")[-1]

    if clean_name == "get_profile":
        return tools.get_profile(household_id=arguments.get("household_id"))

    elif clean_name == "list_billers":
        return tools.list_billers(household_id=arguments.get("household_id"))

    elif clean_name == "list_payees":
        return tools.list_payees(household_id=arguments.get("household_id"))

    elif clean_name == "list_meds":
        return tools.list_meds(household_id=arguments.get("household_id"))

    elif clean_name == "get_due_bills":
        return tools.get_due_bills(household_id=arguments.get("household_id"))

    elif clean_name == "find_slots":
        return tools.find_slots(
            specialty=arguments.get("specialty"),
            date=arguments.get("date"),
        )

    elif clean_name == "book_appointment":
        return tools.book_appointment(
            household_id=arguments.get("household_id"),
            doctor_name=arguments.get("doctor_name", "Dr. Rao"),
            slot_iso=arguments.get("slot_iso", ""),
            patient_name=arguments.get("patient_name", "Kamala"),
        )

    elif clean_name == "set_reminder":
        return tools.set_reminder(
            household_id=arguments.get("household_id"),
            title=arguments.get("title", ""),
            time_iso=arguments.get("time_iso", ""),
        )

    elif clean_name == "pay_bill":
        return tools.pay_bill(
            household_id=arguments.get("household_id"),
            bill_id=arguments.get("bill_id", ""),
            payee_id=arguments.get("payee_id", ""),
            amount=float(arguments.get("amount", 0)),
            idem_key=arguments.get("idem_key", ""),
            assessment_id=arguments.get("assessment_id", ""),
            approval_id=arguments.get("approval_id"),
            note=arguments.get("note"),
            replay_only=arguments.get("idempotent_replay") is True,
        )

    elif clean_name in ("reorder_medicine", "reorder_medicines"):
        med_ids = arguments.get("med_ids") or arguments.get("medicine_ids") or []
        return tools.reorder_medicine(
            household_id=arguments.get("household_id"),
            med_ids=med_ids,
            amount=float(arguments.get("amount", 0)),
            idem_key=arguments.get("idem_key", ""),
            assessment_id=arguments.get("assessment_id", ""),
            approval_id=arguments.get("approval_id"),
            note=arguments.get("note"),
            pharmacy_name=arguments.get("pharmacy_name", "Apollo Pharmacy"),
            replay_only=arguments.get("idempotent_replay") is True,
        )

    # Legacy aliases
    elif clean_name == "pay_utility_bill":
        return tools.pay_utility_bill(
            biller_id=arguments.get("biller_id", ""),
            amount=float(arguments.get("amount", 0)),
            household_id=arguments.get("household_id"),
            approval_id=arguments.get("approval_id"),
        )
    elif clean_name == "check_bill_status":
        return tools.check_bill_status(
            biller_id=arguments.get("biller_id"),
            household_id=arguments.get("household_id"),
        )
    elif clean_name == "list_saved_billers":
        return tools.list_saved_billers(
            household_id=arguments.get("household_id"),
        )
    else:
        return {
            "success": False,
            "error": f"Unknown tool '{tool_name}' in Errands MCP server.",
        }
