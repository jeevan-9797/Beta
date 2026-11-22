"""Seed fictional household HH#KAMALA_01 data for BETA.

Seeds:
- Parent: Kamala (74, Dharwad), Guardian: Arjun (Bengaluru)
- Rules: auto_limit=2000, max_with_approval=25000, frozen=False, cooling_off_seconds=20,
         night_start=23, night_end=6, max_payments_per_hour=2
- Saved Billers/Payees:
    * BESCOM (electricity, bill_id BILL_BESCOM_NOV, amount 1240, due 28 Nov)
    * BWSSB (water)
    * Apollo Pharmacy (known payee)
- Meds: Metformin 500mg, Amlodipine 5mg (reorder total 3800)
"""

from datetime import datetime, timezone
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure backend root is on sys.path
current_dir = Path(__file__).resolve().parent
backend_dir = current_dir.parent
project_dir = backend_dir.parent
for p in [str(project_dir), str(backend_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.common.config import settings
from backend.common.db import (
    format_pk,
    get_db,
    put_item,
    query_all_for_household,
)
from backend.common.models import (
    Biller,
    HouseholdMember,
    HouseholdRules,
    Medicine,
    Payee,
    SKPrefix,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HOUSEHOLD_ID = "HH#KAMALA_01"


def get_seed_payload(household_id: str = HOUSEHOLD_ID) -> List[Dict[str, Any]]:
    """Generate all items for the fictional household."""
    hh_id = format_pk(household_id)

    # 1. Household Members (Parent Kamala, Guardian Arjun)
    parent = HouseholdMember(
        household_id=hh_id,
        member_id="KAMALA",
        name="Kamala",
        role="PARENT",
        age=74,
        location="Dharwad",
        phone="+918362000000",
        relation="Self",
    ).to_item()

    guardian = HouseholdMember(
        household_id=hh_id,
        member_id="ARJUN",
        name="Arjun",
        role="GUARDIAN",
        age=44,
        location="Bengaluru",
        phone="+919876543210",
        relation="Son",
    ).to_item()

    # 2. Financial Guardrails & Safety Rules
    rules = HouseholdRules(
        auto_limit=2000.0,
        max_with_approval=25000.0,
        frozen=False,
        cooling_off_seconds=20,
        night_start=23,
        night_end=6,
        max_payments_per_hour=2,
    ).to_item(hh_id)

    # 2b. Core Config Rule (RULE#CONFIG)
    config_rule = {
        "PK": hh_id,
        "SK": f"{SKPrefix.RULE}CONFIG",
        "frozen": False,
        "auto_limit": 2000.0,
        "max_with_approval": 25000.0,
        "cooling_off_seconds": 20,
        "night_start": 23,
        "night_end": 6,
        "max_payments_per_hour": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entity_type": "RULE",
    }

    # 3. Saved Utility Billers
    bescom = Biller(
        biller_id="BESCOM",
        name="BESCOM Electricity",
        utility_type="electricity",
        bill_id="BILL_BESCOM_NOV",
        amount=1240.0,
        due_date="28 Nov",
        account_no="BESCOM_DHW_7821",
        status="PENDING",
    ).to_item(hh_id)

    bwssb = Biller(
        biller_id="BWSSB",
        name="BWSSB Water Supply",
        utility_type="water",
        bill_id="BILL_BWSSB_NOV",
        amount=450.0,
        due_date="30 Nov",
        account_no="BWSSB_KA_9921",
        status="PENDING",
    ).to_item(hh_id)

    # 4. Known Payees (Merchants)
    apollo = Payee(
        payee_id="APOLLO_PHARMACY",
        name="Apollo Pharmacy",
        category="Pharmacy",
        upi_id="apollopharmacy@icici",
        verified=True,
        trusted=True,
    ).to_item(hh_id)

    # 5. Prescribed Medications (Combined reorder total: ₹3800)
    metformin = Medicine(
        med_id="METFORMIN_500MG",
        name="Metformin 500mg",
        dosage="500mg",
        schedule="Twice daily after meals",
        refill_qty=60,
        unit_price=20.0,
        reorder_total=1200.0,
        prescribed_by="Dr. Kulkarni (Dharwad Clinic)",
    ).to_item(hh_id)

    amlodipine = Medicine(
        med_id="AMLODIPINE_5MG",
        name="Amlodipine 5mg",
        dosage="5mg",
        schedule="Once daily morning",
        refill_qty=60,
        unit_price=43.333,
        reorder_total=2600.0,
        prescribed_by="Dr. Kulkarni (Dharwad Clinic)",
    ).to_item(hh_id)

    # 6. Active Voice Telemetry Assessment (valid for 5 mins)
    now_iso = datetime.now(timezone.utc).isoformat()
    init_assessment = {
        "PK": hh_id,
        "SK": f"{SKPrefix.ASSESSMENT}ASM_KAMALA_INIT",
        "assessment_id": "ASM_KAMALA_INIT",
        "risk_level": "R1",
        "consumed": False,
        "status": "ACTIVE",
        "sentiment": "CALM",
        "confusion_score": 0.05,
        "audio_clarity": 0.95,
        "created_at": now_iso,
        "timestamp": now_iso,
        "entity_type": "ASSESSMENT",
    }

    # 7. Initial Audit Log Entry
    init_audit = {
        "PK": hh_id,
        "SK": f"{SKPrefix.AUDIT}{now_iso}",
        "action": "HOUSEHOLD_INITIALIZED",
        "actor": "SYSTEM:SEED_SCRIPT",
        "details": {
            "household_id": hh_id,
            "parent": "Kamala (74, Dharwad)",
            "guardian": "Arjun (Bengaluru)",
            "rules": {
                "auto_limit": 2000,
                "max_with_approval": 25000,
                "cooling_off_seconds": 20,
            },
        },
        "timestamp": now_iso,
        "risk_score": 0.0,
        "status": "SUCCESS",
        "entity_type": "AUDIT",
    }

    return [
        parent,
        guardian,
        rules,
        config_rule,
        bescom,
        bwssb,
        apollo,
        metformin,
        amlodipine,
        init_assessment,
        init_audit,
    ]


def seed_household_data(household_id: str = HOUSEHOLD_ID) -> int:
    """Seed all initial records into DynamoDB or local mock store."""
    hh_id = format_pk(household_id)
    items = get_seed_payload(hh_id)

    logger.info(
        f"Seeding {len(items)} records for household {hh_id} (LOCAL_MOCK_DB={settings.LOCAL_MOCK_DB})..."
    )

    for item in items:
        put_item(item)
        logger.info(f"  -> Saved item: SK={item.get('SK')}")

    # Verify seeded records
    seeded_items = query_all_for_household(hh_id)
    logger.info(f"✅ Successfully verified {len(seeded_items)} records for {hh_id}:")
    for itm in seeded_items:
        sk = itm.get("SK", "")
        entity = itm.get("entity_type", "")
        summary = itm.get("name") or itm.get("action") or itm.get("auto_limit") or ""
        logger.info(f"   [{entity}] SK={sk} | Info={summary}")

    return len(seeded_items)


if __name__ == "__main__":
    count = seed_household_data()
    print(f"\nSeed completed successfully: {count} items written to database.")
