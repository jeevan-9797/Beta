"""Errands MCP Tool Implementations.

Complete suite of errand execution tools:
- get_profile: household profile, limits, and status
- list_billers: saved utility billers
- list_payees: saved merchants and payees
- list_meds: prescription medicine catalog
- get_due_bills: pending unpaid bills
- find_slots: clinic appointment slots
- book_appointment: Google Calendar appointment booking + receipt
- set_reminder: Google Calendar reminder + DynamoDB persistence
- pay_bill: BBPS utility bill payment with strict idempotency
- reorder_medicine: pharmacy reorder with idempotency and Cedar guardrails
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional
import uuid

from backend.common.audit import log_audit
from backend.common.calendar_client import create_calendar_event
from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_item,
    put_item,
    update_item,
    get_billers,
    get_payees,
    get_meds,
    get_household_members,
    get_household_rules,
    mark_assessment_consumed,
)
from backend.common.models import SKPrefix

logger = logging.getLogger(__name__)


def get_profile(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve household profile details: parent name, guardian name, spending limits, and status."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    members = get_household_members(hh_id)
    rules = get_household_rules(hh_id) or {}

    parent = next((m for m in members if m.get("role") == "PARENT"), {})
    guardian = next((m for m in members if m.get("role") == "GUARDIAN"), {})

    return {
        "success": True,
        "household_id": hh_id,
        "parent": {
            "name": parent.get("name", "Kamala"),
            "age": parent.get("age", 74),
            "location": parent.get("location", "Dharwad"),
            "phone": parent.get("phone"),
        },
        "guardian": {
            "name": guardian.get("name", "Arjun"),
            "relation": guardian.get("relation", "Son"),
            "location": guardian.get("location", "Bengaluru"),
            "phone": guardian.get("phone", "+919876543210"),
        },
        "limits": {
            "auto_limit": rules.get("auto_limit", 2000.0),
            "max_with_approval": rules.get("max_with_approval", 25000.0),
            "cooling_off_seconds": rules.get("cooling_off_seconds", 20),
            "night_curfew": f"{rules.get('night_start', 23)}:00 - {rules.get('night_end', 6)}:00 IST",
            "max_payments_per_hour": rules.get("max_payments_per_hour", 2),
        },
        "frozen": bool(rules.get("frozen", False)),
    }


def list_billers(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve saved utility billers registered for the household."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    billers = get_billers(hh_id)
    return {
        "success": True,
        "household_id": hh_id,
        "billers": billers,
        "count": len(billers),
    }


def list_payees(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve saved payees and merchants registered for the household."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    payees = get_payees(hh_id)
    return {
        "success": True,
        "household_id": hh_id,
        "payees": payees,
        "count": len(payees),
    }


def list_meds(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve registered chronic prescription medications and refill prices."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    meds = get_meds(hh_id)
    return {
        "success": True,
        "household_id": hh_id,
        "meds": meds,
        "count": len(meds),
    }


def get_due_bills(household_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve all pending unpaid bills for the household."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    all_billers = get_billers(hh_id)
    due_bills = [b for b in all_billers if b.get("status") != "PAID"]
    total_due = sum(float(b.get("amount", 0.0)) for b in due_bills)

    return {
        "success": True,
        "household_id": hh_id,
        "due_bills": due_bills,
        "total_due": total_due,
        "count": len(due_bills),
    }


def find_slots(
    specialty: Optional[str] = None,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    """Find available doctor appointment slots at local clinics."""
    target_date = date or (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    spec = (specialty or "General Physician").strip()

    # Clinic slots in Dharwad
    available_slots = [
        {
            "doctor_name": "Dr. Rao",
            "specialty": "Cardiologist",
            "clinic": "Dharwad Heart Care Clinic",
            "slot_iso": f"{target_date}T10:30:00+05:30",
            "display_time": "10:30 AM IST",
            "consultation_fee": 600.0,
        },
        {
            "doctor_name": "Dr. Kulkarni",
            "specialty": "General Physician",
            "clinic": "Dharwad Senior Health Clinic",
            "slot_iso": f"{target_date}T16:00:00+05:30",
            "display_time": "04:00 PM IST",
            "consultation_fee": 400.0,
        },
        {
            "doctor_name": "Dr. Desai",
            "specialty": "Endocrinologist",
            "clinic": "Hubli-Dharwad Diabetes Center",
            "slot_iso": f"{target_date}T11:45:00+05:30",
            "display_time": "11:45 AM IST",
            "consultation_fee": 700.0,
        },
    ]

    # Filter by specialty if requested
    filtered = [
        s for s in available_slots
        if spec.lower() in s["specialty"].lower() or spec.lower() in s["doctor_name"].lower()
    ]
    slots_to_return = filtered if filtered else available_slots

    return {
        "success": True,
        "specialty_searched": spec,
        "date": target_date,
        "slots": slots_to_return,
        "count": len(slots_to_return),
    }


def book_appointment(
    household_id: Optional[str] = None,
    doctor_name: str = "Dr. Rao",
    slot_iso: str = "",
    patient_name: str = "Kamala",
) -> Dict[str, Any]:
    """Book a clinic appointment, creates a Google Calendar event, and records receipt."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    now_iso = datetime.now(timezone.utc).isoformat()
    if not slot_iso:
        slot_iso = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=5, minute=0, second=0, microsecond=0
        ).isoformat()

    tx_id = f"APT_{uuid.uuid4().hex[:10].upper()}"
    summary = f"Clinic Appointment: {patient_name} with {doctor_name}"
    description = (
        f"BETA Scheduled Appointment\n"
        f"Patient: {patient_name} (Household: {hh_id})\n"
        f"Doctor: {doctor_name}\n"
        f"Booking ID: {tx_id}\n"
        f"Booked via BETA Voice Assistant"
    )

    # Create Real Google Calendar event (with graceful mock fallback)
    is_real, cal_event_id, event_data = create_calendar_event(
        summary=summary,
        description=description,
        start_iso=slot_iso,
        duration_minutes=30,
    )

    # Save RECEIPT#<tx_id>
    receipt = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RECEIPT}{tx_id}",
        "receipt_id": tx_id,
        "doctor_name": doctor_name,
        "patient_name": patient_name,
        "slot_iso": slot_iso,
        "calendar_event_id": cal_event_id,
        "google_calendar_synced": is_real,
        "provider": "GOOGLE_CALENDAR",
        "status": "CONFIRMED",
        "booked_at": now_iso,
        "timestamp": now_iso,
        "entity_type": "RECEIPT",
    }
    put_item(receipt)

    # Log AUDIT#
    log_audit(
        household_id=hh_id,
        action="TOOL_BOOK_APPOINTMENT",
        actor="MCP:ERRANDS",
        details={
            "tx_id": tx_id,
            "doctor_name": doctor_name,
            "slot_iso": slot_iso,
            "calendar_event_id": cal_event_id,
            "google_calendar_synced": is_real,
        },
        risk_score=0.1,
        status="CONFIRMED",
    )

    return {
        "success": True,
        "status": "CONFIRMED",
        "appointment_id": tx_id,
        "doctor_name": doctor_name,
        "patient_name": patient_name,
        "slot_iso": slot_iso,
        "calendar_event_id": cal_event_id,
        "google_calendar_synced": is_real,
        "receipt": receipt,
        "message": f"Appointment successfully confirmed with {doctor_name} for {patient_name} at {slot_iso}.",
    }


def set_reminder(
    household_id: Optional[str] = None,
    title: str = "",
    time_iso: str = "",
) -> Dict[str, Any]:
    """Create a reminder in Google Calendar and persist in DynamoDB."""
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    now_iso = datetime.now(timezone.utc).isoformat()
    if not time_iso:
        time_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    reminder_id = f"REM_{uuid.uuid4().hex[:8].upper()}"
    summary = f"Reminder: {title}"
    description = f"BETA Reminder for {hh_id}: {title}"

    is_real, cal_event_id, _ = create_calendar_event(
        summary=summary,
        description=description,
        start_iso=time_iso,
        duration_minutes=15,
    )

    reminder_item = {
        "PK": hh_id,
        "SK": f"{SKPrefix.REMINDER}{reminder_id}",
        "reminder_id": reminder_id,
        "title": title,
        "time_iso": time_iso,
        "calendar_event_id": cal_event_id,
        "google_calendar_synced": is_real,
        "status": "ACTIVE",
        "created_at": now_iso,
        "entity_type": "REMINDER",
    }
    put_item(reminder_item)

    log_audit(
        household_id=hh_id,
        action="TOOL_SET_REMINDER",
        actor="MCP:ERRANDS",
        details={
            "reminder_id": reminder_id,
            "title": title,
            "time_iso": time_iso,
            "calendar_event_id": cal_event_id,
        },
        risk_score=0.0,
        status="ACTIVE",
    )

    return {
        "success": True,
        "status": "ACTIVE",
        "reminder_id": reminder_id,
        "title": title,
        "time_iso": time_iso,
        "calendar_event_id": cal_event_id,
        "google_calendar_synced": is_real,
        "message": f"Reminder set: '{title}' at {time_iso}.",
    }


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
    - Marks assessment_id as consumed and used: True on ASSESS#<id>.
    - Writes RECEIPT#<tx_id> labelled provider: 'MOCK_BBPS'.
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

    # 4. Mark assessment_id as consumed and used: True
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


def reorder_medicine(
    household_id: Optional[str] = None,
    med_ids: Optional[List[str]] = None,
    amount: float = 0.0,
    idem_key: str = "",
    assessment_id: str = "",
    approval_id: Optional[str] = None,
    note: Optional[str] = None,
    pharmacy_name: Optional[str] = "Apollo Pharmacy",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Reorder chronic prescription medicines with strict idempotency and Cedar guardrails.

    - Protected by Cedar rules (R2 auto-pay <= 2000, R3 approved-pay <= 25000).
    - Idempotent on PAYMENT#<idem_key>.
    - Consumes ASSESS#<assessment_id> (used = True).
    - Writes RECEIPT#<tx_id> labelled provider: 'MOCK_PHARMACY'.
    """
    hh_id = format_pk(household_id or settings.DEFAULT_HOUSEHOLD_ID)
    amount = float(amount)
    now_iso = datetime.now(timezone.utc).isoformat()
    pharmacy = pharmacy_name or "Apollo Pharmacy"
    clean_med_ids = med_ids or []

    if not idem_key:
        idem_key = f"IDEM_MED_{int(amount)}_{now_iso[:10]}"

    payment_sk = f"{SKPrefix.PAYMENT}{idem_key}"

    # 1. Strict Idempotency Check
    existing_payment = get_item(hh_id, payment_sk)
    if existing_payment:
        tx_id = existing_payment.get("tx_id", "")
        receipt = get_item(hh_id, f"{SKPrefix.RECEIPT}{tx_id}")
        logger.info(
            f"Idempotent medicine order replay for {hh_id} / idem_key: {idem_key}. Returning cached receipt."
        )
        return {
            "success": True,
            "status": "ALREADY_PROCESSED",
            "idempotent_replay": True,
            "idem_key": idem_key,
            "tx_id": tx_id,
            "amount": existing_payment.get("amount", amount),
            "total_amount": existing_payment.get("amount", amount),
            "provider": "MOCK_PHARMACY",
            "receipt": receipt or existing_payment,
            "message": f"Medicine order already processed for idempotency key '{idem_key}'. Cached receipt returned.",
        }

    # 2. Generate transaction ID
    tx_id = f"TX_PHARM_{uuid.uuid4().hex[:10].upper()}"

    # 3. Write PAYMENT#<idem_key>
    payment_record = {
        "PK": hh_id,
        "SK": payment_sk,
        "idem_key": idem_key,
        "tx_id": tx_id,
        "med_ids": clean_med_ids,
        "payee_name": pharmacy,
        "amount": amount,
        "assessment_id": assessment_id,
        "approval_id": approval_id,
        "note": note,
        "status": "SUCCESS",
        "provider": "MOCK_PHARMACY",
        "timestamp": now_iso,
        "entity_type": "PAYMENT",
    }
    put_item(payment_record)

    # 4. Mark assessment_id as consumed / used: True
    if assessment_id:
        mark_assessment_consumed(hh_id, assessment_id)

    # 5. Write RECEIPT#<tx_id> labelled MOCK_PHARMACY
    receipt_record = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RECEIPT}{tx_id}",
        "receipt_id": tx_id,
        "med_ids": clean_med_ids,
        "pharmacy": pharmacy,
        "amount": amount,
        "idem_key": idem_key,
        "assessment_id": assessment_id,
        "approval_id": approval_id,
        "provider": "MOCK_PHARMACY",
        "status": "CONFIRMED",
        "delivery_estimate": "Tomorrow by 2:00 PM to Dharwad",
        "ordered_at": now_iso,
        "timestamp": now_iso,
        "entity_type": "RECEIPT",
    }
    put_item(receipt_record)

    # 6. Audit log
    log_audit(
        household_id=hh_id,
        action="TOOL_REORDER_MEDICINE",
        actor="MCP:ERRANDS",
        details={
            "tx_id": tx_id,
            "pharmacy": pharmacy,
            "amount": amount,
            "med_ids": clean_med_ids,
            "idem_key": idem_key,
            "assessment_id": assessment_id,
            "provider": "MOCK_PHARMACY",
        },
        risk_score=0.2,
        status="SUCCESS",
    )

    return {
        "success": True,
        "status": "CONFIRMED",
        "idempotent_replay": False,
        "idem_key": idem_key,
        "tx_id": tx_id,
        "pharmacy": pharmacy,
        "amount": amount,
        "total_amount": amount,
        "med_ids": clean_med_ids,
        "provider": "MOCK_PHARMACY",
        "delivery_estimate": "Tomorrow by 2:00 PM to Dharwad",
        "receipt": receipt_record,
        "message": f"Order confirmed with {pharmacy} for ₹{amount:,.2f}. Delivery expected tomorrow.",
    }


# Backward compatibility aliases
def pay_utility_bill(
    biller_id: str,
    amount: float,
    household_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
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
    return reorder_medicine(
        household_id=household_id,
        med_ids=medicine_ids,
        amount=3800.0,
        idem_key=f"MED_ORDER_{uuid.uuid4().hex[:8].upper()}",
        assessment_id="ASM_AUTO_LEGACY",
        approval_id=approval_id,
        pharmacy_name=pharmacy_name,
    )


def check_bill_status(
    biller_id: Optional[str] = None,
    household_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_due_bills(household_id)


def list_saved_billers(household_id: Optional[str] = None) -> Dict[str, Any]:
    return list_billers(household_id)
