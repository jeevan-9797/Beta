"""Bedrock AgentCore Gateway Response Interceptor.

Sanitizes agent output before speech synthesis / UI rendering for elderly parents.
Redacts internal technical traces, sensitive tokens, or system errors, and records
auditing events for completed agent interactions.
"""

import logging
import re
from typing import Any, Dict, Optional

from backend.common.audit import log_audit
from backend.common.db import format_pk

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
                },
                risk_score=0.0 if execution_status == "SUCCESS" else 0.4,
                status=execution_status,
            )

        return {
            "sanitized_response": clean_text,
            "status": execution_status,
            "tool_result": tool_result,
        }
