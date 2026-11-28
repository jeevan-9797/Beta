"""Guardian Console Lambda and local FastAPI routes."""

import base64
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote

from backend.common.audit import log_audit, query_audit_trail
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    update_item,
    query_by_prefix,
    get_household_rules,
    get_household_config,
    get_household_members,
    get_billers,
    get_payees,
    put_item_if_absent,
)
from backend.interceptors.request_interceptor import parse_timestamp
from backend.common.models import ApprovalStatus, SKPrefix
from backend.common.receipts import deliver_receipt_notification
from backend.guardian_api.whatsapp_webhook import process_whatsapp_message

logger = logging.getLogger(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Content-Type": "application/json",
}
CONFIG_KEY = f"{SKPrefix.RULE}CONFIG"
LEGACY_CONFIG_KEY = f"{SKPrefix.RULE}FINANCIAL"
ACTION_STATUSES = {"APPROVE": "APPROVED", "DECLINE": "DECLINED", "CANCEL": "CANCELLED"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _approval_view(item, now):
    status = item.get("status", "PENDING")
    deadline = parse_timestamp(item.get("executable_at") or item.get("cooling_off_until"))
    if deadline is None and status in {"APPROVED", "COOLING_OFF", "APPROVED_COOLING_OFF"}:
        approved = parse_timestamp(item.get("approved_at") or item.get("responded_at"))
        if approved is not None:
            deadline = approved + timedelta(seconds=int(item.get("cooling_off_seconds", settings.COOLING_OFF_SECONDS)))
    expiry_times = (parse_timestamp(item.get("ttl")), parse_timestamp(item.get("expires_at")))
    remaining = 0
    if status in {"PENDING", "APPROVED", "COOLING_OFF", "APPROVED_COOLING_OFF", "READY"} and any(
        expiry is not None and now >= expiry for expiry in expiry_times
    ):
        status = "EXPIRED"
    elif status in {"APPROVED", "COOLING_OFF", "APPROVED_COOLING_OFF"}:
        remaining = max(0, int(deadline.timestamp()) - int(now.timestamp())) if deadline else 0
        status = "APPROVED" if remaining else "READY"
    return {
        **item,
        "approval_id": item.get("approval_id") or item["SK"].removeprefix(SKPrefix.APPROVAL),
        "status": status,
        "executable_at": int(deadline.timestamp()) if deadline else None,
        "seconds_remaining": remaining,
    }


def _console_state(household, rules, now):
    profile = {"parent": None, "guardian": None}
    for member in get_household_members(household):
        key = member["SK"].removeprefix(SKPrefix.MEMBER).lower()
        role = key if key in profile else str(member.get("role", "")).lower()
        if role in profile and (profile[role] is None or key == role):
            profile[role] = member
    approvals = [_approval_view(item, now) for item in query_by_prefix(household, SKPrefix.APPROVAL)]
    approvals.sort(key=lambda item: parse_timestamp(item.get("requested_at") or item.get("created_at"))
                   or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return {
        "household_id": household, "profile": profile,
        "rules": {
            "frozen": bool(rules.get("frozen", False)),
            "auto_limit": rules.get("auto_limit", 2000),
            "max_with_approval": rules.get("max_with_approval", 25000),
            "cooling_off_seconds": rules.get("cooling_off_seconds", settings.COOLING_OFF_SECONDS),
        },
        "approvals": [item for item in approvals if item["status"] in {"PENDING", "APPROVED", "READY"}],
    }


def _feed(household):
    feed = []
    for item in query_audit_trail(household, limit=50):
        details = item.get("details") or {}
        action = item.get("action", "")
        tool = item.get("tool_name") or details.get("tool_name")
        if not tool and action.startswith("TOOL_"):
            tool = action.removeprefix("TOOL_").lower()
        feed.append({
            **item, "tool_name": tool, "timestamp": item.get("timestamp"),
            "computed_facts": item.get("computed_facts") or details.get("computed_facts", {}),
            "cedar_decision": item.get("cedar_decision") or details.get("cedar_decision"),
            "mcp_message_id": item.get("mcp_message_id", details.get("mcp_message_id")),
            "status": item.get("status"),
        })
    return feed


def _change_approval(household, approval_id, payload, rules, now):
    action = payload.get("action")
    if not isinstance(action, str) or action not in ACTION_STATUSES:
        raise ApiError(400, "action must be APPROVE, DECLINE or CANCEL")
    key = f"{SKPrefix.APPROVAL}{approval_id.removeprefix(SKPrefix.APPROVAL)}"
    item = get_item(household, key)
    if not item:
        raise ApiError(404, "Approval request not found")
    target = ACTION_STATUSES[action]
    current = _approval_view(item, now)
    if item.get("status") == target and current["status"] != "EXPIRED":
        return current
    allowed = {"PENDING", "APPROVED", "READY"} if action == "CANCEL" else {"PENDING"}
    if current["status"] not in allowed:
        raise ApiError(409, f"Approval is {current['status']}")
    updated_at = now.isoformat()
    changes = {"status": target, "guardian_response": target, "responded_at": updated_at, "updated_at": updated_at}
    if action == "APPROVE":
        cooldown = int(rules.get("cooling_off_seconds", item.get("cooling_off_seconds") or settings.COOLING_OFF_SECONDS))
        epoch = int(now.timestamp())
        changes.update(approved_at=epoch, executable_at=epoch + cooldown, cooling_off_seconds=cooldown)
    updated = update_item(household, key, changes, expected_status=item["status"])
    if updated is None:
        raise ApiError(409, "Approval changed. Refresh and retry")
    log_audit(household, f"REST_APPROVAL_{target}", "GUARDIAN:DASHBOARD",
              {"approval_id": current["approval_id"], "amount": item.get("amount")}, status=target)
    return _approval_view(updated, now)


def _change_rules(household, changes, rules, now):
    updates = {**changes, "updated_at": now.isoformat()}
    # Legacy households store their rules under RULE#FINANCIAL.
    created = not get_item(household, CONFIG_KEY) and put_item_if_absent({
        **rules, "PK": household, "SK": CONFIG_KEY, **updates,
    })
    if not created:
        update_item(household, CONFIG_KEY, updates)
    if updates.get("frozen") is False:
        legacy = get_item(household, LEGACY_CONFIG_KEY)
        if legacy and legacy.get("frozen") is True:
            update_item(household, LEGACY_CONFIG_KEY, updates)
    return updates


def _rule_patch(payload):
    if not payload or set(payload) - {"auto_limit", "cooling_off_seconds"}:
        raise ApiError(400, "Provide auto_limit and/or cooling_off_seconds")
    if "auto_limit" in payload:
        amount = payload["auto_limit"]
        if type(amount) not in (int, float) or not 0 <= amount < float("inf"):
            raise ApiError(400, "auto_limit must be a finite number greater than or equal to 0")
    if "cooling_off_seconds" in payload:
        duration = payload["cooling_off_seconds"]
        max_duration = (datetime.max.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days * 86400
        if type(duration) is not int or not 5 <= duration <= max_duration:
            raise ApiError(400, "cooling_off_seconds must be a supported integer greater than or equal to 5")
    return payload


def _request_json(event):
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True).decode("utf-8")
        payload = json.loads(body)
    except (ValueError, TypeError) as error:
        raise ApiError(400, "Request body must be a JSON object") from error
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object")
    return payload


def _console_route(event, method, path):
    parts = path.strip("/").split("/")
    if len(parts) < 4 or parts[:2] != ["api", "household"] or not parts[2]:
        raise ApiError(404, "Route not found")
    household = format_pk(unquote(parts[2]))
    route = parts[3:]
    rules = get_household_config(household)
    if not rules:
        raise ApiError(404, "Household not found")
    now = datetime.now(timezone.utc)
    if method == "GET" and len(route) == 1:
        if route[0] == "state":
            return _console_state(household, rules, now)
        if route[0] == "feed":
            return {"feed": _feed(household)}
        if route[0] == "receipts":
            receipts = query_by_prefix(household, SKPrefix.RECEIPT)
            receipts.sort(key=lambda item: item.get("timestamp") or item.get("paid_at") or
                          item.get("booked_at") or item.get("ordered_at") or "", reverse=True)
            return {"receipts": receipts[:20]}
    if method == "POST" and len(route) == 3 and route[0] == "approvals" and route[2] == "action":
        return {"approval": _change_approval(household, unquote(route[1]), _request_json(event), rules, now)}
    if method == "POST" and route == ["freeze"]:
        frozen = _request_json(event).get("frozen")
        if type(frozen) is not bool:
            raise ApiError(400, "frozen must be a boolean")
        result = _change_rules(household, {"frozen": frozen}, rules, now)
        log_audit(household, "EMERGENCY_FREEZE_ACTIVATED" if frozen else "EMERGENCY_FREEZE_LIFTED",
                  "GUARDIAN:DASHBOARD", {"frozen": frozen})
        return result
    if method == "PATCH" and route == ["rules"]:
        return _change_rules(household, _rule_patch(_request_json(event)), rules, now)
    raise ApiError(404, "Route not found")


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Route API Gateway v1 and v2 proxy events to the Guardian Console API."""
    http = (event.get("requestContext") or {}).get("http") or {}
    method = http.get("method") or event.get("httpMethod")
    if not method and "beta_receipt_notification" in event:
        notification = event["beta_receipt_notification"]
        deliver_receipt_notification(notification["household_id"], notification["receipt_id"])
        return {"statusCode": 204, "body": ""}
    path = event.get("rawPath") or event.get("path") or http.get("path") or ""
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}
    try:
        payload, status = _console_route(event, method, path), 200
    except ApiError as error:
        payload, status = {"error": str(error)}, error.status
    except Exception:
        logger.exception("Guardian Console request failed")
        payload, status = {"error": "Internal server error"}, 500
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(payload, allow_nan=False)}

# FastAPI conditional import
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import PlainTextResponse
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None
    BaseModel = object

if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="BETA Guardian REST API",
        description="Oversight, authorization, and emergency freeze control for elder care voice assistant.",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class ApprovalActionRequest(BaseModel):
        action: str  # APPROVE or REJECT
        guardian_id: Optional[str] = "ARJUN"
        notes: Optional[str] = None

    class FreezeRequest(BaseModel):
        freeze: bool
        reason: Optional[str] = "Manual guardian freeze toggle"

    @app.get("/health")
    def health_check() -> Dict[str, str]:
        return {"status": "ok", "service": "BETA Guardian API"}

    @app.get("/api/household/{household_id}/status")
    def get_household_status(household_id: str) -> Dict[str, Any]:
        hh_id = format_pk(household_id)
        rules = get_household_rules(hh_id) or {}
        members = get_household_members(hh_id)
        billers = get_billers(hh_id)
        approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
        pending_approvals = [
            a for a in approvals
            if a.get("status") in (ApprovalStatus.PENDING.value, ApprovalStatus.COOLING_OFF.value)
        ]

        return {
            "household_id": hh_id,
            "rules": rules,
            "members": members,
            "pending_approvals": pending_approvals,
            "billers": billers,
        }

    @app.get("/api/household/{household_id}/approvals")
    def list_approvals(household_id: str) -> List[Dict[str, Any]]:
        hh_id = format_pk(household_id)
        approvals = query_by_prefix(hh_id, SKPrefix.APPROVAL)
        approvals.sort(key=lambda x: str(x.get("requested_at", "")), reverse=True)
        return approvals

    @app.post("/api/household/{household_id}/approvals/{approval_id}/action")
    def handle_approval_action(
        household_id: str,
        approval_id: str,
        payload: ApprovalActionRequest,
    ) -> Dict[str, Any]:
        hh_id = format_pk(household_id)
        sk = (
            approval_id
            if approval_id.startswith(SKPrefix.APPROVAL)
            else f"{SKPrefix.APPROVAL}{approval_id}"
        )
        item = get_item(hh_id, sk)
        if not item:
            raise HTTPException(status_code=404, detail="Approval request not found")

        action_norm = payload.action.upper()
        if action_norm not in ("APPROVE", "REJECT"):
            raise HTTPException(
                status_code=400, detail="Action must be APPROVE or REJECT"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        cooling_off_until = item.get("cooling_off_until")
        if action_norm == "APPROVE" and cooling_off_until and now_iso < cooling_off_until:
            raise HTTPException(
                status_code=425,
                detail=f"Cooling-off delay active until {cooling_off_until}",
            )

        new_status = (
            ApprovalStatus.APPROVED.value
            if action_norm == "APPROVE"
            else ApprovalStatus.REJECTED.value
        )
        updated = update_item(
            hh_id,
            sk,
            {
                "status": new_status,
                "guardian_response": action_norm,
                "notes": payload.notes,
                "responded_at": now_iso,
            },
        )

        log_audit(
            household_id=hh_id,
            action=f"REST_APPROVAL_{new_status}",
            actor=f"GUARDIAN:{payload.guardian_id}",
            details={"approval_id": approval_id, "amount": item.get("amount")},
            risk_score=0.1 if action_norm == "APPROVE" else 0.5,
            status=new_status,
        )

        return {"success": True, "approval": updated}

    @app.post("/api/household/{household_id}/emergency_freeze")
    def emergency_freeze(
        household_id: str,
        payload: FreezeRequest,
    ) -> Dict[str, Any]:
        hh_id = format_pk(household_id)
        rules = get_household_rules(hh_id) or {}
        now_iso = datetime.now(timezone.utc).isoformat()

        update_item(
            hh_id,
            f"{SKPrefix.RULE}FINANCIAL",
            {
                "frozen": payload.freeze,
                "updated_at": now_iso,
                "freeze_reason": payload.reason,
            },
        )

        action_name = "EMERGENCY_FREEZE_ACTIVATED" if payload.freeze else "EMERGENCY_FREEZE_LIFTED"
        log_audit(
            household_id=hh_id,
            action=action_name,
            actor="GUARDIAN:DASHBOARD",
            details={"frozen": payload.freeze, "reason": payload.reason},
            risk_score=1.0 if payload.freeze else 0.2,
            status="SUCCESS",
        )

        return {
            "success": True,
            "household_id": hh_id,
            "frozen": payload.freeze,
            "updated_at": now_iso,
        }

    @app.get("/api/household/{household_id}/audit_log")
    def get_audit_trail(household_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        hh_id = format_pk(household_id)
        return query_audit_trail(hh_id, limit=limit)

    @app.post("/api/webhook/whatsapp")
    async def twilio_whatsapp_webhook(request: Request) -> PlainTextResponse:
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        reply_text = process_whatsapp_message(
            from_number=form.get("From", ["whatsapp:+919876543210"])[0],
            body=form.get("Body", [""])[0],
            household_id=settings.DEFAULT_HOUSEHOLD_ID,
        )
        return PlainTextResponse(content=reply_text, media_type="text/plain")

else:
    # Fallback stub when running in minimalist test environments without fastapi installed
    app = None
