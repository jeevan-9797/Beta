"""Errands MCP Tool Implementations.

Handles bill payments (BBPS gateway simulation), utility bills (BESCOM, BWSSB),
chronic medicine reorders (Apollo Pharmacy), and receipt generation with strict idempotency.
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
    mark_assessment_consumed,
)
from backend.common.models import SKPrefix

logger = logging.getLogger(__name__)


def pay_bill(
    household_id: Optional[str] = None,
    bill_id: str = "",
    payee_id: str = "",
    amount: float = 0.0,
    idem_key: str = "",
    assessment_id: str = "",
    approval_id: Optional[str] = None,
    note: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute bill payment via BBPS gateway with strict idempotency.

    - Idempotency on idem_key: If PAYMENT#<idem_key> exists, returns existing receipt without re-charging.
    - Marks assessment_id as consumed (single-use).
    - Writes RECEIPT#<tx_id> labelled MOCK_BBPS.
    """
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    amount = float(amount)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not idem_key:
        idem_key = f"IDEM_{bill_id}_{int(amount)}_{now_iso[:10]}"

    payment_sk = f"{SKPrefix.PAYMENT}{idem_key}"

    # 1. Strict Idempotency Check: if PAYMENT#<idem_key> exists, return cached receipt
    existing_payment = get_item(hh_id, payment_sk)
    if existing_payment:
        tx_id = existing_payment.get("tx_id", "")
        receipt = get_item(hh_id, f"{SKPrefix.RECEIPT}{tx_id}")
        logger.info(
            f"Idempotent payment replay detected for {hh_id} / idem_key: {idem_key}. Returning existing receipt."
        )
        return {
            "success": True,
            "status": "ALREADY_PROCESSED",
            "idempotent_replay": True,
            "idem_key": idem_key,
            "tx_id": tx_id,
            "amount": existing_payment.get("amount", amount),
            "provider": "MOCK_BBPS",
            "receipt": receipt or existing_payment,
            "message": f"Payment already processed for idempotency key '{idem_key}'. Cached receipt returned.",
        }

    # 2. Generate new transaction ID
    tx_id = f"TX_BBPS_{uuid.uuid4().hex[:10].upper()}"

    # 3. Write PAYMENT#<idem_key> item (idempotency anchor)
    payment_record = {
        "PK": hh_id,
        "SK": payment_sk,
        "idem_key": idem_key,
        "tx_id": tx_id,
        "bill_id": bill_id,
        "payee_id": payee_id,
        "amount": amount,
        "assessment_id": assessment_id,
        "approval_id": approval_id,
        "note": note,
        "status": "SUCCESS",
        "provider": "MOCK_BBPS",
        "timestamp": now_iso,
        "entity_type": "PAYMENT",
    }
    put_item(payment_record)

    # 4. Mark assessment_id as consumed (single-use enforcement)
    if assessment_id:
        mark_assessment_consumed(hh_id, assessment_id)

    # 5. Write RECEIPT#<tx_id> labelled MOCK_BBPS
    receipt_record = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RECEIPT}{tx_id}",
        "receipt_id": tx_id,
        "bill_id": bill_id,
        "payee_id": payee_id,
        "amount": amount,
        "idem_key": idem_key,
        "assessment_id": assessment_id,
        "approval_id": approval_id,
        "provider": "MOCK_BBPS",
        "status": "PAID",
        "paid_at": now_iso,
        "timestamp": now_iso,
        "entity_type": "RECEIPT",
    }
    put_item(receipt_record)

    # 6. Update biller status if matching biller is registered
    for b_key in [payee_id, bill_id]:
        if b_key:
            target_sk = f"{SKPrefix.BILLER}{str(b_key).strip().upper()}"
            if get_item(hh_id, target_sk):
                update_item(hh_id, target_sk, {"status": "PAID", "last_paid_at": now_iso})

    # 7. Audit log
    log_audit(
        household_id=hh_id,
        action="TOOL_PAY_BILL",
        actor="MCP:ERRANDS",
        details={
            "tx_id": tx_id,
            "bill_id": bill_id,
            "payee_id": payee_id,
            "amount": amount,
            "idem_key": idem_key,
            "assessment_id": assessment_id,
            "provider": "MOCK_BBPS",
        },
        risk_score=0.1,
        status="SUCCESS",
    )

    return {
        "success": True,
        "status": "SUCCESS",
        "idempotent_replay": False,
        "idem_key": idem_key,
        "tx_id": tx_id,
        "bill_id": bill_id,
        "payee_id": payee_id,
        "amount": amount,
        "provider": "MOCK_BBPS",
        "receipt": receipt_record,
        "message": f"Successfully paid ₹{amount:,.2f} to {payee_id or bill_id} via MOCK_BBPS. Receipt #{tx_id}.",
    }


def pay_utility_bill(
    biller_id: str,
    amount: float,
    household_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute utility bill payment for a registered biller (wrapper around pay_bill)."""
    return pay_bill(
        household_id=household_id,
        bill_id=biller_id,
        payee_id=biller_id,
        amount=amount,
        idem_key=f"UTIL_{biller_id}_{int(amount)}",
        assessment_id="ASM_AUTO_LEGACY",
        approval_id=approval_id,
    )


def reorder_medicines(
    medicine_ids: List[str],
    pharmacy_name: Optional[str] = "Apollo Pharmacy",
    household_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reorder prescribed chronic medicines from a verified pharmacy."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    pharmacy = pharmacy_name or "Apollo Pharmacy"

    all_meds = get_meds(hh_id)
    med_lookup = {m.get("med_id", "").upper(): m for m in all_meds}

    selected_meds = []
    total_amount = 0.0

    for m_id in medicine_ids:
        clean_id = m_id.strip().upper()
        if clean_id in med_lookup:
            med = med_lookup[clean_id]
            selected_meds.append(med)
            total_amount += float(
                med.get("reorder_total")
                or (med.get("unit_price", 0) * med.get("refill_qty", 1))
            )
        else:
            return {
                "success": False,
                "error": f"Medicine '{m_id}' is not in the patient's registered prescription list.",
            }

    order_id = f"MED_{uuid.uuid4().hex[:10].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()
    idem_key = f"MED_{order_id}"

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

    receipt = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RECEIPT}{order_id}",
        "receipt_id": order_id,
        "pharmacy": pharmacy,
        "medicines": [
            {"name": m.get("name"), "dosage": m.get("dosage"), "qty": m.get("refill_qty")}
            for m in selected_meds
        ],
        "amount": total_amount,
        "provider": "MOCK_BBPS",
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
