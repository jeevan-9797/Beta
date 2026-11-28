"""Bedrock AgentCore Gateway Response Interceptor.

Sanitizes agent output before speech synthesis / UI rendering for elderly parents.
Redacts internal technical traces, sensitive tokens, or system errors, and records
auditing events for completed agent interactions.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import format_pk, get_household_config, get_item

logger = logging.getLogger(__name__)

# Patterns for sensitive tokens or technical jargon to redact from voice synthesis
REDACTION_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    (r"(?i)sk-[a-zA-Z0-9]{32,}", "[REDACTED_API_KEY]"),
    (r"(?i)auth_token\s*=\s*['\"][^'\"]+['\"]", "[REDACTED_AUTH_TOKEN]"),
    (r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b", "[REDACTED_AADHAAR]"),  # 12-digit Aadhaar
]


class ResponseInterceptor:
    """AgentCore Gateway Response Interceptor for safety and elderly-friendly output."""

    def __init__(self, household_id: Optional[str] = None):
        self.household_id = format_pk(household_id or "")

    def sanitize_text(self, text: str) -> str:
        """Strip internal identifiers, stack traces, and sensitive PII."""
        if not text:
            return ""

        sanitized = text
        for pattern, replacement in REDACTION_PATTERNS:
            sanitized = re.sub(pattern, replacement, sanitized)

        # Simplify technical error messages into clear, reassuring language for elderly
        if "ClientError" in sanitized or "InternalServerError" in sanitized:
            return (
                "Nanage ondu kshana samaya beku (Please give me a moment). "
                "There was a brief network delay. I am checking the details for you."
            )

        return sanitized

    def intercept(
        self,
        tool_name: Optional[str],
        tool_result: Any,
        final_response: str,
        execution_status: str = "SUCCESS",
        *,
        cedar_decision: Optional[str] = None,
        mcp_message_id: Any = None,
    ) -> Dict[str, Any]:
        """Process response before returning to Bedrock AgentCore voice output."""
        clean_text = self.sanitize_text(final_response)

        # Record audit log
        if self.household_id:
            log_audit(
                household_id=self.household_id,
                action=f"RESPONSE_DELIVERED:{tool_name or 'GENERAL'}",
                actor="SYSTEM:RESPONSE_INTERCEPTOR",
                details={
                    "tool_name": tool_name,
                    "status": execution_status,
                    "response_length": len(clean_text),
                    "cedar_decision": cedar_decision,
                    "mcp_message_id": mcp_message_id,
                },
                risk_score=0.0 if execution_status == "SUCCESS" else 0.4,
                status=execution_status,
            )

        return {
            "sanitized_response": clean_text,
            "status": execution_status,
            "tool_result": tool_result,
        }


def _sanitize(value: Any, interceptor: ResponseInterceptor) -> Any:
    if isinstance(value, str):
        return interceptor.sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize(item, interceptor) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item, interceptor) for key, item in value.items()}
    return value


def _money_tool(tool: Dict[str, Any]) -> bool:
    name = tool.get("name", "")
    return not name.startswith(("Guardian___", "Shield___")) and name.rsplit("___", 1)[-1] in {
        "pay_bill", "reorder_medicine", "reorder_medicines",
    }


def _decode_body(body: Any) -> Any:
    if isinstance(body, str):
        try:
            return json.loads(body)
        except ValueError:
            pass
    return body


def _policy_decision(body: Dict[str, Any]) -> Optional[str]:
    error = body.get("error")
    if isinstance(error, dict) and error.get("code") == -32002 and str(error.get("message", "")).startswith("Tool Execution Denied:"):
        return "DENY"
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("isError") is True and any(
        isinstance(block, dict) and str(block.get("text", "")).startswith("AuthorizeActionException - Tool Execution Denied:")
        for block in result.get("content") or []
    ):
        return "DENY"
    if result.get("isError") is False and isinstance(result.get("content"), list):
        return "ALLOW"
    return None


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Transform the AgentCore response while preserving the MCP envelope."""
    mcp = event["mcp"]
    response = mcp["gatewayResponse"]
    request = _decode_body(mcp.get("gatewayRequest", {}).get("body"))
    if not isinstance(request, dict):
        request = {}
    params = request.get("params") or {}
    default_household = settings.DEFAULT_HOUSEHOLD_ID
    body = response.get("body")
    decoded = _decode_body(body)
    result = decoded.get("result") if isinstance(decoded, dict) else None
    if isinstance(result, dict) and isinstance(result.get("tools"), list):
        # A tool list has no authenticated household selector. Use the deployment's household.
        rules = get_item(default_household, "RULE#CONFIG") or get_household_config(default_household) or {}
        if rules.get("frozen") is True:
            decoded = {**decoded, "result": {**result, "tools": [
                tool for tool in result["tools"] if not _money_tool(tool)
            ]}}
    elif request.get("method") == "tools/call" and isinstance(decoded, dict):
        household = (params.get("arguments") or {}).get("household_id") or default_household
        interceptor = ResponseInterceptor(household)
        decision = _policy_decision(decoded)
        decoded = {key: _sanitize(value, interceptor) if key in {"result", "error"} else value
                   for key, value in decoded.items()}
        result = decoded.get("result")
        failed = "error" in decoded or (isinstance(result, dict) and result.get("isError"))
        status = "DENIED" if decision == "DENY" else "FAILED" if failed else "SUCCESS"
        interceptor.intercept(params.get("name"), result, json.dumps(result), status,
                              cedar_decision=decision, mcp_message_id=request.get("id"))

    transformed = {"headers": response.get("headers") or {}, "body": body}
    if "statusCode" in response:
        transformed["statusCode"] = response["statusCode"]
    if isinstance(decoded, dict):
        transformed["body"] = json.dumps(decoded) if isinstance(body, str) else decoded
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayResponse": transformed}}
