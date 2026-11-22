"""Audit logging module for BETA backend.

Records structured audit events in DynamoDB single-table design with TTL
for security compliance, guardian oversight, and transaction traceability.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional
import uuid

from backend.common.config import settings
from backend.common.db import format_pk, put_item, query_by_prefix
from backend.common.models import AuditEvent, SKPrefix

logger = logging.getLogger(__name__)


def log_audit(
    household_id: str,
    action: str,
    actor: str,
    details: Dict[str, Any],
    risk_score: float = 0.0,
    status: str = "SUCCESS",
    retention_days: int = 90,
) -> Dict[str, Any]:
    """Log an audit event to DynamoDB and application log.

    Args:
        household_id: Target household identifier (e.g. 'HH#KAMALA_01').
        action: Action name (e.g., 'TOOL_PAY_BILL', 'GUARDIAN_APPROVAL_GRANTED', 'FRAUD_FLAGGED').
        actor: Actor performing action ('PARENT:KAMALA', 'GUARDIAN:ARJUN', 'SYSTEM:AGENT_CORE', 'SYSTEM:SHIELD').
        details: Metadata dictionary associated with the action.
        risk_score: Assessed risk score (0.0 = safe, 1.0 = high risk).
        status: Action status ('SUCCESS', 'FAILED', 'BLOCKED', 'PENDING_APPROVAL').
        retention_days: DynamoDB TTL retention days.

    Returns:
        The stored audit event dictionary.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    ttl_epoch = int((now + timedelta(days=retention_days)).timestamp())

    event = AuditEvent(
        household_id=household_id,
        action=action,
        actor=actor,
        details=details,
        timestamp=timestamp,
        risk_score=risk_score,
        status=status,
        ttl=ttl_epoch,
    )

    item = event.to_item()
    try:
        put_item(item)
    except Exception as e:
        logger.error(f"Failed to persist audit log to DynamoDB: {e}")

    logger.info(
        f"[AUDIT] {timestamp} | Household={household_id} | Actor={actor} | Action={action} | Status={status} | Risk={risk_score}"
    )
    return item


def query_audit_trail(
    household_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """Retrieve audit events for a household, sorted newest first."""
    pk = format_pk(household_id)
    items = query_by_prefix(pk, SKPrefix.AUDIT)
    items.sort(key=lambda x: str(x.get("timestamp", x.get("SK", ""))), reverse=True)
    return items[:limit]
