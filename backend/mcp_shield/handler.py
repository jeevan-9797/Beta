"""MCP Shield server request handler."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from backend.mcp_shield import tools

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "tool_schema.json"


def get_tool_definitions() -> Dict[str, Any]:
    """Load and return MCP tool definitions schema."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch tool execution to appropriate Python function."""
    logger.info(f"Shield MCP executing tool: {tool_name} with arguments {arguments}")

    if tool_name == "screen_scam_risk":
        return tools.screen_scam_risk(
            text=arguments.get("text", ""),
            household_id=arguments.get("household_id"),
        )
    elif tool_name == "verify_payee_trust":
        return tools.verify_payee_trust(
            payee_name=arguments.get("payee_name", ""),
            upi_id=arguments.get("upi_id"),
            household_id=arguments.get("household_id"),
        )
    elif tool_name == "evaluate_cooling_off":
        return tools.evaluate_cooling_off(
            approval_id=arguments.get("approval_id", ""),
            household_id=arguments.get("household_id"),
        )
    elif tool_name == "trigger_emergency_freeze":
        return tools.trigger_emergency_freeze(
            reason=arguments.get("reason", "Suspicious activity detected"),
            household_id=arguments.get("household_id"),
        )
    else:
        return {
            "success": False,
            "error": f"Unknown tool '{tool_name}' in Shield MCP server.",
        }
