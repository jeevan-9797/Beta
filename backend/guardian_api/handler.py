"""FastAPI REST API Handler for Guardian Dashboard and Twilio Webhooks."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from backend.common.audit import log_audit, query_audit_trail
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    update_item,
    query_by_prefix,
    get_household_rules,
    get_household_members,
    get_billers,
    get_payees,
)
from backend.common.models import ApprovalStatus, SKPrefix
from backend.guardian_api.whatsapp_webhook import process_whatsapp_message

logger = logging.getLogger(__name__)

# FastAPI conditional import
try:
    from fastapi import FastAPI, Form, HTTPException, Request, Response
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
    async def twilio_whatsapp_webhook(
        From: str = Form("whatsapp:+919876543210"),
        Body: str = Form(""),
    ) -> PlainTextResponse:
        reply_text = process_whatsapp_message(
            from_number=From,
            body=Body,
            household_id=settings.DEFAULT_HOUSEHOLD_ID,
        )
        return PlainTextResponse(content=reply_text, media_type="text/plain")

else:
    # Fallback stub when running in minimalist test environments without fastapi installed
    app = None
