#!/usr/bin/env python3
"""
seed_data.py - Single Source of Truth Seed & State Reset Tool for BETA
Track 1: Make MCP Matter

Manages DynamoDB seed records for household HH#kamala:
  - --export-json: Refreshes seed/kamala_household.json
  - --load-db: Batch-writes seed records into DynamoDB
  - --reset-state: Resets frozen=false, resets BESCOM to UNPAID,
                   and deletes test artifacts (APPROVAL#, AUDIT#, RECEIPT#)
"""

import os
import sys
import json
import argparse
from pathlib import Path
from decimal import Decimal
from typing import List, Dict, Any

# Load environment variables
try:
    from dotenv import load_dotenv
    # Look for .env in current directory or workspace root
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    pass

# Rich formatting helper with plain fallback
try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
    def log_info(msg: str): console.print(f"[bold cyan][INFO][/bold cyan] {msg}")
    def log_success(msg: str): console.print(f"[bold green][SUCCESS][/bold green] {msg}")
    def log_warn(msg: str): console.print(f"[bold yellow][WARNING][/bold yellow] {msg}")
    def log_error(msg: str): console.print(f"[bold red][ERROR][/bold red] {msg}")
except ImportError:
    def log_info(msg: str): print(f"[INFO] {msg}")
    def log_success(msg: str): print(f"[SUCCESS] {msg}")
    def log_warn(msg: str): print(f"[WARNING] {msg}")
    def log_error(msg: str): print(f"[ERROR] {msg}")

# Authoritative In-Memory Seed Dataset for HH#kamala
KAMALA_SEED_ITEMS: List[Dict[str, Any]] = [
    {
        "PK": "HH#kamala",
        "SK": "MEMBER#parent",
        "name": "Kamala",
        "age": 74,
        "city": "Dharwad",
        "preferred_lang": "hi-IN",
        "phone": "+919000000001",
        "guardian_sk": "MEMBER#guardian",
    },
    {
        "PK": "HH#kamala",
        "SK": "MEMBER#guardian",
        "name": "Rohan",
        "relationship": "Son",
        "city": "Bengaluru",
        "whatsapp_number": "+919999999999",
        "_comment": "NOTE: Replace with live Twilio sandbox demo number before stage run",
    },
    {
        "PK": "HH#kamala",
        "SK": "BILLER#bescom",
        "biller_name": "BESCOM Electricity",
        "aliases": ["bijli", "electricity", "light bill"],
        "consumer_no": "104928371",
        "amount_due": 1240,
        "due_date": "2026-11-28",
        "payee_known": True,
        "status": "UNPAID",
    },
    {
        "PK": "HH#kamala",
        "SK": "PAYEE#apollo",
        "payee_name": "Apollo Pharmacy",
        "upi_id": "apollopharmacy@icici",
        "payee_known": True,
        "category": "pharmacy",
    },
    {
        "PK": "HH#kamala",
        "SK": "MED#telma40",
        "med_name": "Telma 40",
        "dosage": "1 tablet after breakfast",
        "strips_remaining": 2,
        "reorder_qty_strips": 6,
        "reorder_cost_inr": 3800,
        "pharmacy_sk": "PAYEE#apollo",
    },
    {
        "PK": "HH#kamala",
        "SK": "RULE#default",
        "auto_approve_limit_inr": 2000,
        "hard_cap_inr": 25000,
        "quiet_hours_ist": [23, 6],
        "max_payments_per_hour": 2,
        "cooling_off_seconds": 20,
        "approval_expiry_seconds": 7200,
        "frozen": False,
    },
]


def get_json_file_path() -> Path:
    return Path(__file__).resolve().parent / "kamala_household.json"


def export_json(output_path: Path = None) -> Path:
    target = output_path or get_json_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(KAMALA_SEED_ITEMS, f, indent=2, ensure_ascii=False)
    log_success(f"Wrote {len(KAMALA_SEED_ITEMS)} seed items to {target.resolve()}")
    return target


def get_dynamodb_table():
    try:
        import boto3
    except ImportError:
        log_error("boto3 is not installed. Install requirements with: pip install -r requirements.txt")
        sys.exit(1)

    table_name = os.getenv("DYNAMODB_TABLE_NAME")
    region = os.getenv("AWS_REGION", "ap-south-1")

    if not table_name:
        log_error("DYNAMODB_TABLE_NAME is not set in environment or .env file.")
        sys.exit(1)

    log_info(f"Connecting to DynamoDB table '{table_name}' in region '{region}'...")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(table_name)


def load_db():
    table = get_dynamodb_table()
    json_path = get_json_file_path()

    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            items = json.load(f, parse_float=Decimal)
        log_info(f"Loaded {len(items)} items from {json_path}")
    else:
        items = KAMALA_SEED_ITEMS
        log_warn(f"{json_path} not found. Using in-memory seed records.")

    log_info(f"Batch-writing {len(items)} items to DynamoDB...")
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
            log_info(f"  -> PUT PK={item.get('PK')} SK={item.get('SK')}")

    log_success("DynamoDB seed loading complete!")


def reset_state():
    table = get_dynamodb_table()
    household_id = os.getenv("TEST_HOUSEHOLD_ID", "HH#kamala")

    try:
        from boto3.dynamodb.conditions import Key
    except ImportError:
        import boto3
        from boto3.dynamodb.conditions import Key

    log_warn(f"Resetting state for {household_id}...")

    # 1. Reset RULE#default -> frozen: false
    log_info("Ensuring RULE#default has frozen = False...")
    table.update_item(
        Key={"PK": household_id, "SK": "RULE#default"},
        UpdateExpression="SET frozen = :val",
        ExpressionAttributeValues={":val": False},
    )

    # 2. Reset BILLER#bescom -> status: UNPAID
    log_info("Resetting BILLER#bescom status = UNPAID...")
    table.update_item(
        Key={"PK": household_id, "SK": "BILLER#bescom"},
        UpdateExpression="SET #st = :val",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":val": "UNPAID"},
    )

    # 3. Purge dynamic runtime records: APPROVAL#, AUDIT#, RECEIPT#
    purge_prefixes = ["APPROVAL#", "AUDIT#", "RECEIPT#"]
    deleted_count = 0

    for prefix in purge_prefixes:
        log_info(f"Scanning and purging items with SK starting with '{prefix}'...")
        response = table.query(
            KeyConditionExpression=Key("PK").eq(household_id) & Key("SK").begins_with(prefix)
        )
        items_to_delete = response.get("Items", [])

        while True:
            for it in items_to_delete:
                table.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
                deleted_count += 1
                log_info(f"  Deleted {it.get('SK')}")

            if "LastEvaluatedKey" in response:
                response = table.query(
                    KeyConditionExpression=Key("PK").eq(household_id) & Key("SK").begins_with(prefix),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items_to_delete = response.get("Items", [])
            else:
                break

    log_success(f"State reset complete! Cleared {deleted_count} runtime items. Ready for demo run.")


def main():
    parser = argparse.ArgumentParser(
        description="BETA Seed & Demo State Manager for DynamoDB (HH#kamala)"
    )
    parser.add_argument(
        "--export-json",
        action="store_true",
        help="Export or refresh seed/kamala_household.json cleanly formatted.",
    )
    parser.add_argument(
        "--load-db",
        action="store_true",
        help="Batch-write seed items into DynamoDB using DYNAMODB_TABLE_NAME.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset frozen to false, BESCOM to UNPAID, and purge APPROVAL#, AUDIT#, RECEIPT# items.",
    )

    args = parser.parse_args()

    if not (args.export_json or args.load_db or args.reset_state):
        parser.print_help()
        sys.exit(0)

    if args.export_json:
        export_json()

    if args.load_db:
        load_db()

    if args.reset_state:
        reset_state()


if __name__ == "__main__":
    main()
