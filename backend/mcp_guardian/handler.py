"""MCP Guardian server request handler."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from backend.mcp_guardian import tools

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "tool_schema.json"


def get_tool_definitions() -> Dict[str, Any]:
    """Load and return MCP tool definitions schema."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch tool execution to appropriate Python function."""
    logger.info(f"Guardian MCP executing tool: {tool_name} with arguments {arguments}")

    if tool_name == "request_guardian_approval":
        return tools.request_guardian_approval(
            errand_type=arguments.get("errand_type", "EXPENSE"),
            payee_name=arguments.get("payee_name", ""),
            amount=float(arguments.get("amount", 0)),
            reason=arguments.get("reason"),
            household_id=arguments.get("household_id"),
        )
    elif tool_name == "check_approval_status":
        return tools.check_approval_status(
            approval_id=arguments.get("approval_id", ""),
            household_id=arguments.get("household_id"),
        )
    elif tool_name == "send_guardian_alert":
        return tools.send_guardian_alert(
            alert_type=arguments.get("alert_type", "SCAM_SUSPECTED"),
            message=arguments.get("message", ""),
            severity=arguments.get("severity", "HIGH"),
            household_id=arguments.get("household_id"),
        )
    elif tool_name == "get_household_rules":
        return tools.get_household_rules_tool(
            household_id=arguments.get("household_id"),
        )
    else:
        return {
            "success": False,
            "error": f"Unknown tool '{tool_name}' in Guardian MCP server.",
        }
