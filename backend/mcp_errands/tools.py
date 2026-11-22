"""Errands MCP Tool Implementations.

Handles bill payments (BESCOM, BWSSB), chronic medicine reorders (Apollo Pharmacy),
bill checking, and receipt generation with single-table DynamoDB state.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from backend.common.audit import log_audit
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    put_item,
    update_item,
    get_billers,
    get_payees,
    get_meds,
)
from backend.common.models import SKPrefix

logger = logging.getLogger(__name__)


def pay_utility_bill(
    biller_id: str,
    amount: float,
    household_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute utility bill payment for a registered biller."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    biller_sk = f"{SKPrefix.BILLER}{biller_id.strip().upper()}"
    biller_item = get_item(hh_id, biller_sk)

    if not biller_item:
        return {
            "success": False,
            "error": f"Biller '{biller_id}' is not registered for household {hh_id}.",
        }

    tx_id = f"TX_{uuid.uuid4().hex[:12].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()
    idem_key = f"{biller_id}_{now_iso[:10]}_{int(amount)}"

    # Create payment record
    payment_record = {
        "PK": hh_id,
        "SK": f"{SKPrefix.PAYMENT}{idem_key}",
        "tx_id": tx_id,
        "biller_id": biller_id,
        "payee_name": biller_item.get("name", biller_id),
        "amount": amount,
        "status": "SUCCESS",
        "timestamp": now_iso,
        "approval_id": approval_id,
        "entity_type": "PAYMENT",
    }
    put_item(payment_record)

    # Create digital receipt
    receipt = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RECEIPT}{tx_id}",
        "receipt_id": tx_id,
        "biller_id": biller_id,
        "amount": amount,
        "bill_id": biller_item.get("bill_id", "N/A"),
        "paid_at": now_iso,
        "status": "PAID",
        "entity_type": "RECEIPT",
    }
    put_item(receipt)

    # Mark biller as paid
    update_item(hh_id, biller_sk, {"status": "PAID", "last_paid_at": now_iso})

    log_audit(
        household_id=hh_id,
        action="TOOL_PAY_UTILITY_BILL",
        actor="MCP:ERRANDS",
        details={
            "biller_id": biller_id,
            "amount": amount,
            "tx_id": tx_id,
            "bill_id": biller_item.get("bill_id"),
        },
        risk_score=0.1,
        status="SUCCESS",
    )

    return {
        "success": True,
        "tx_id": tx_id,
        "biller_name": biller_item.get("name", biller_id),
        "amount_paid": amount,
        "bill_id": biller_item.get("bill_id"),
        "receipt_id": tx_id,
        "message": f"Successfully paid ₹{amount} to {biller_item.get('name', biller_id)}. Receipt #{tx_id}.",
    }


def reorder_medicines(
    medicine_ids: List[str],
    pharmacy_name: Optional[str] = "Apollo Pharmacy",
    household_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reorder prescribed chronic medicines from a verified pharmacy."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    pharmacy = pharmacy_name or "Apollo Pharmacy"

    # Fetch medicines
    all_meds = get_meds(hh_id)
    med_lookup = {
        m.get("med_id", "").upper(): m
        for m in all_meds
    }

    selected_meds = []
    total_amount = 0.0

    for m_id in medicine_ids:
        clean_id = m_id.strip().upper()
        if clean_id in med_lookup:
            med = med_lookup[clean_id]
            selected_meds.append(med)
            total_amount += float(med.get("reorder_total") or (med.get("unit_price", 0) * med.get("refill_qty", 1)))
        else:
            return {
                "success": False,
                "error": f"Medicine '{m_id}' is not in the patient's registered prescription list.",
            }

    order_id = f"MED_{uuid.uuid4().hex[:10].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()
    idem_key = f"MED_{order_id}"

    # Payment record
    payment_record = {
        "PK": hh_id,
        "SK": f"{SKPrefix.PAYMENT}{idem_key}",
        "tx_id": order_id,
        "payee_name": pharmacy,
        "amount": total_amount,
        "status": "SUCCESS",
        "timestamp": now_iso,
        "approval_id": approval_id,
        "items": [m.get("name") for m in selected_meds],
        "entity_type": "PAYMENT",
    }
    put_item(payment_record)

    # Receipt record
    receipt = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RECEIPT}{order_id}",
        "order_id": order_id,
        "pharmacy": pharmacy,
        "medicines": [
            {"name": m.get("name"), "dosage": m.get("dosage"), "qty": m.get("refill_qty")}
            for m in selected_meds
        ],
        "amount": total_amount,
        "ordered_at": now_iso,
        "delivery_address": "Dharwad, Karnataka",
        "entity_type": "RECEIPT",
    }
    put_item(receipt)

    log_audit(
        household_id=hh_id,
        action="TOOL_REORDER_MEDICINES",
        actor="MCP:ERRANDS",
        details={
            "order_id": order_id,
            "pharmacy": pharmacy,
            "total_amount": total_amount,
            "medicines": [m.get("name") for m in selected_meds],
        },
        risk_score=0.2,
        status="SUCCESS",
    )

    return {
        "success": True,
        "order_id": order_id,
        "pharmacy": pharmacy,
        "total_amount": total_amount,
        "items": [f"{m.get('name')} ({m.get('dosage')})" for m in selected_meds],
        "delivery_estimate": "Tomorrow by 2:00 PM to Dharwad home",
        "message": f"Placed order with {pharmacy} for ₹{total_amount}. Delivery expected tomorrow.",
    }


def check_bill_status(
    biller_id: Optional[str] = None,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve bill details and pending amounts."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    billers = get_billers(hh_id)

    if biller_id:
        target = biller_id.strip().upper()
        matching = [
            b for b in billers
            if b.get("biller_id", "").upper() == target or b.get("name", "").upper() == target
        ]
        if not matching:
            return {"success": False, "error": f"Biller '{biller_id}' not found."}
        return {"success": True, "biller": matching[0]}

    return {
        "success": True,
        "household_id": hh_id,
        "billers": billers,
        "pending_count": sum(1 for b in billers if b.get("status") == "PENDING"),
    }


def list_saved_billers(household_id: Optional[str] = None) -> Dict[str, Any]:
    """List all registered utilities and known payees."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    billers = get_billers(hh_id)
    payees = get_payees(hh_id)

    return {
        "success": True,
        "household_id": hh_id,
        "billers": billers,
        "payees": payees,
    }
