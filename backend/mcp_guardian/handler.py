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

    clean_name = tool_name
    if "___" in clean_name:
        clean_name = clean_name.split("___")[-1]

    if clean_name in ("request_approval", "request_guardian_approval"):
        return tools.request_approval(
            household_id=arguments.get("household_id"),
            action_type=arguments.get("action_type") or arguments.get("errand_type", "PAYMENT"),
            payee_name=arguments.get("payee_name", ""),
            amount=float(arguments.get("amount", 0)),
            reason=arguments.get("reason", ""),
            bill_id=arguments.get("bill_id"),
            med_ids=arguments.get("med_ids"),
        )
    elif clean_name in ("get_approval_status", "check_approval_status"):
        return tools.get_approval_status(
            household_id=arguments.get("household_id"),
            approval_id=arguments.get("approval_id", ""),
        )
    elif clean_name in ("notify_guardian", "send_guardian_alert"):
        return tools.notify_guardian(
            household_id=arguments.get("household_id"),
            severity=arguments.get("severity", "INFO"),
            message=arguments.get("message", ""),
        )
    elif clean_name in ("get_guard_status", "get_household_rules"):
        return tools.get_guard_status(
            household_id=arguments.get("household_id"),
        )
    else:
        return {
            "success": False,
            "error": f"Unknown tool '{tool_name}' in Guardian MCP server.",
        }
