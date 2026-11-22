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

    if clean_name == "pay_bill":
        return tools.pay_bill(
            household_id=arguments.get("household_id"),
            bill_id=arguments.get("bill_id", ""),
            payee_id=arguments.get("payee_id", ""),
            amount=float(arguments.get("amount", 0)),
            idem_key=arguments.get("idem_key", ""),
            assessment_id=arguments.get("assessment_id", ""),
            approval_id=arguments.get("approval_id"),
            note=arguments.get("note"),
        )
    elif clean_name == "pay_utility_bill":
        return tools.pay_utility_bill(
            biller_id=arguments.get("biller_id", ""),
            amount=float(arguments.get("amount", 0)),
            household_id=arguments.get("household_id"),
            approval_id=arguments.get("approval_id"),
        )
    elif clean_name == "reorder_medicines":
        return tools.reorder_medicines(
            medicine_ids=arguments.get("medicine_ids", []),
            pharmacy_name=arguments.get("pharmacy_name", "Apollo Pharmacy"),
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
