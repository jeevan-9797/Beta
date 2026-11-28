"""DynamoDB single-table helpers and local offline mock database for BETA."""

import json
import logging
import os
from threading import RLock
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from backend.common.config import settings
from backend.common.models import SKPrefix

logger = logging.getLogger(__name__)

# Boto3 conditional import
try:
    import boto3
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    Key = None
    ClientError = Exception


def format_pk(household_id: str) -> str:
    """Ensure PK has HH# prefix."""
    hh = household_id or settings.DEFAULT_HOUSEHOLD_ID
    return hh if hh.startswith("HH#") else f"HH#{hh}"


def _to_dynamo_friendly(item: Any) -> Any:
    """Convert Python floats to Decimal for DynamoDB serialization."""
    if isinstance(item, float):
        return Decimal(str(item))
    if isinstance(item, dict):
        return {k: _to_dynamo_friendly(v) for k, v in item.items()}
    if isinstance(item, list):
        return [_to_dynamo_friendly(v) for v in item]
    return item


def _from_dynamo_friendly(item: Any) -> Any:
    """Convert DynamoDB Decimals back to standard Python float/int."""
    if isinstance(item, Decimal):
        return int(item) if item % 1 == 0 else float(item)
    if isinstance(item, dict):
        return {k: _from_dynamo_friendly(v) for k, v in item.items()}
    if isinstance(item, list):
        return [_from_dynamo_friendly(v) for v in item]
    return item


class MockDatabase:
    """Thread-safe in-memory and JSON file-backed single-table mock for offline runs."""

    def __init__(self, persistence_file: Optional[str] = None):
        self.persistence_file = persistence_file or settings.MOCK_DB_FILE
        self._store: Dict[str, Dict[str, Any]] = {}
        self._update_lock = RLock()
        self._load_from_file()

    def _make_key(self, pk: str, sk: str) -> str:
        return f"{pk}###{sk}"

    def _load_from_file(self) -> None:
        if self.persistence_file and os.path.isfile(self.persistence_file):
            try:
                with open(self.persistence_file, "r", encoding="utf-8") as f:
                    self._store = json.load(f)
            except Exception as e:
                logger.warning(
                    f"Could not load mock DB file {self.persistence_file}: {e}"
                )
                self._store = {}

    def _persist_to_file(self) -> None:
        if self.persistence_file:
            try:
                parent = Path(self.persistence_file).parent
                parent.mkdir(parents=True, exist_ok=True)
                with open(self.persistence_file, "w", encoding="utf-8") as f:
                    json.dump(self._store, f, indent=2, default=str)
            except Exception as e:
                logger.warning(
                    f"Could not write to mock DB file {self.persistence_file}: {e}"
                )

    def get_item(self, pk: str, sk: str) -> Optional[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        key = self._make_key(pk_formatted, sk)
        item = self._store.get(key)
        return dict(item) if item else None

    def put_item(self, item: Dict[str, Any], *, if_absent: bool = False) -> Optional[Dict[str, Any]]:
        pk = format_pk(item.get("PK", ""))
        sk = str(item.get("SK", ""))
        if not pk or not sk:
            raise ValueError("Item must contain valid PK and SK")

        normalized = dict(item)
        normalized["PK"] = pk
        normalized["SK"] = sk
        key = self._make_key(pk, sk)
        with self._update_lock:
            if if_absent and key in self._store:
                return None
            self._store[key] = normalized
            self._persist_to_file()
        return normalized

    def delete_item(self, pk: str, sk: str) -> bool:
        pk_formatted = format_pk(pk)
        key = self._make_key(pk_formatted, sk)
        if key in self._store:
            del self._store[key]
            self._persist_to_file()
            return True
        return False

    def query_by_prefix(
        self, pk: str, sk_prefix: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        results = []
        for key, item in self._store.items():
            if item.get("PK") == pk_formatted and str(item.get("SK", "")).startswith(sk_prefix):
                results.append(dict(item))

        # Sort by SK for consistent ordering
        results.sort(key=lambda x: str(x.get("SK", "")))
        if limit is not None and limit > 0:
            return results[:limit]
        return results

    def query_pk(self, pk: str) -> List[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        results = [
            dict(item)
            for item in self._store.values()
            if item.get("PK") == pk_formatted
        ]
        results.sort(key=lambda x: str(x.get("SK", "")))
        return results

    def update_item(
        self, pk: str, sk: str, updates: Dict[str, Any], *, expected_status: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        key = self._make_key(pk_formatted, sk)
        with self._update_lock:
            item = self._store.get(key)
            if not item or (expected_status is not None and item.get("status") != expected_status):
                return None
            item.update(updates)
            self._persist_to_file()
            return dict(item)

    def batch_write_items(self, items: List[Dict[str, Any]]) -> None:
        for item in items:
            self.put_item(item)

    def put_payment_and_receipt(self, payment: Dict[str, Any], receipt: Dict[str, Any]) -> bool:
        with self._update_lock:
            payment_key = self._make_key(payment["PK"], payment["SK"])
            if payment_key in self._store:
                return False
            self._store[payment_key] = dict(payment)
            self._store[self._make_key(receipt["PK"], receipt["SK"])] = dict(receipt)
            self._persist_to_file()
            return True

    def clear(self) -> None:
        self._store.clear()
        self._persist_to_file()


class DynamoDatabase:
    """Production AWS DynamoDB table helper."""

    def __init__(self, table_name: str, region_name: str):
        if not BOTO3_AVAILABLE:
            raise RuntimeError(
                "boto3 is not installed. Please install boto3 or set LOCAL_MOCK_DB=true."
            )
        self.region_name = region_name
        self.table_name = table_name
        self._resource = boto3.resource("dynamodb", region_name=region_name)
        self._table = self._resource.Table(table_name)

    def get_item(self, pk: str, sk: str) -> Optional[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        try:
            response = self._table.get_item(
                Key={"PK": pk_formatted, "SK": sk}, ConsistentRead=True,
            )
            item = response.get("Item")
            return _from_dynamo_friendly(item) if item else None
        except ClientError as e:
            logger.error(f"DynamoDB get_item error: {e}")
            raise

    def put_item(self, item: Dict[str, Any], *, if_absent: bool = False) -> Optional[Dict[str, Any]]:
        pk = format_pk(item.get("PK", ""))
        sk = str(item.get("SK", ""))
        if not pk or not sk:
            raise ValueError("Item must contain valid PK and SK")

        normalized = dict(item)
        normalized["PK"] = pk
        normalized["SK"] = sk
        dynamo_item = _to_dynamo_friendly(normalized)
        try:
            condition = {"ConditionExpression": "attribute_not_exists(PK)"} if if_absent else {}
            self._table.put_item(Item=dynamo_item, **condition)
            return normalized
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            logger.error(f"DynamoDB put_item error: {e}")
            raise

    def delete_item(self, pk: str, sk: str) -> bool:
        pk_formatted = format_pk(pk)
        try:
            self._table.delete_item(Key={"PK": pk_formatted, "SK": sk})
            return True
        except ClientError as e:
            logger.error(f"DynamoDB delete_item error: {e}")
            raise

    def query_by_prefix(
        self, pk: str, sk_prefix: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        try:
            key_condition = Key("PK").eq(pk_formatted) & Key("SK").begins_with(
                sk_prefix
            )
            query_kwargs: Dict[str, Any] = {
                "KeyConditionExpression": key_condition, "ConsistentRead": True,
            }
            if limit:
                query_kwargs["Limit"] = limit

            response = self._table.query(**query_kwargs)
            items = response.get("Items", [])
            while response.get("LastEvaluatedKey") and (limit is None or len(items) < limit):
                query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                if limit is not None:
                    query_kwargs["Limit"] = limit - len(items)
                response = self._table.query(**query_kwargs)
                items.extend(response.get("Items", []))
            return [_from_dynamo_friendly(item) for item in items]
        except ClientError as e:
            logger.error(f"DynamoDB query_by_prefix error: {e}")
            raise

    def query_pk(self, pk: str) -> List[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        try:
            response = self._table.query(
                KeyConditionExpression=Key("PK").eq(pk_formatted)
            )
            items = response.get("Items", [])
            return [_from_dynamo_friendly(item) for item in items]
        except ClientError as e:
            logger.error(f"DynamoDB query_pk error: {e}")
            raise

    def update_item(
        self, pk: str, sk: str, updates: Dict[str, Any], *, expected_status: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        pk_formatted = format_pk(pk)
        if not updates:
            return self.get_item(pk_formatted, sk)

        expr_parts = []
        names = {}
        values = {}

        for idx, (k, v) in enumerate(updates.items()):
            field_name = f"#f{idx}"
            value_name = f":v{idx}"
            expr_parts.append(f"{field_name} = {value_name}")
            names[field_name] = k
            values[value_name] = _to_dynamo_friendly(v)

        update_expr = "SET " + ", ".join(expr_parts)
        condition = {}
        if expected_status is not None:
            names["#expected_status"] = "status"
            values[":expected_status"] = expected_status
            condition["ConditionExpression"] = "#expected_status = :expected_status"

        try:
            response = self._table.update_item(
                Key={"PK": pk_formatted, "SK": sk},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
                **condition,
            )
            return _from_dynamo_friendly(response.get("Attributes"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            logger.error(f"DynamoDB update_item error: {e}")
            raise

    def batch_write_items(self, items: List[Dict[str, Any]]) -> None:
        try:
            with self._table.batch_writer() as batch:
                for item in items:
                    pk = format_pk(item.get("PK", ""))
                    sk = str(item.get("SK", ""))
                    normalized = dict(item)
                    normalized["PK"] = pk
                    normalized["SK"] = sk
                    batch.put_item(Item=_to_dynamo_friendly(normalized))
        except ClientError as e:
            logger.error(f"DynamoDB batch_write_items error: {e}")
            raise

    def put_payment_and_receipt(self, payment: Dict[str, Any], receipt: Dict[str, Any]) -> bool:
        try:
            self._table.meta.client.transact_write_items(TransactItems=[
                {"Put": {"TableName": self.table_name, "Item": _to_dynamo_friendly(payment),
                         "ConditionExpression": "attribute_not_exists(PK)"}},
                {"Put": {"TableName": self.table_name, "Item": _to_dynamo_friendly(receipt)}},
            ])
        except ClientError as error:
            reasons = error.response.get("CancellationReasons") or []
            if (error.response["Error"]["Code"] == "TransactionCanceledException" and reasons
                    and reasons[0].get("Code") == "ConditionalCheckFailed"):
                return False
            raise
        return True


# Singleton Database Instance Manager
_db_instance: Optional[Union[MockDatabase, DynamoDatabase]] = None


def get_db(force_mock: Optional[bool] = None) -> Union[MockDatabase, DynamoDatabase]:
    """Factory retrieving active database instance (Mock or real DynamoDB)."""
    global _db_instance
    use_mock = settings.LOCAL_MOCK_DB if force_mock is None else force_mock

    if _db_instance is None:
        if use_mock:
            _db_instance = MockDatabase(settings.MOCK_DB_FILE)
        else:
            _db_instance = DynamoDatabase(
                settings.DYNAMODB_TABLE, settings.AWS_REGION
            )
    return _db_instance


def reset_mock_db() -> MockDatabase:
    """Reset and reinitialize mock DB for testing."""
    global _db_instance
    _db_instance = MockDatabase(settings.MOCK_DB_FILE)
    _db_instance.clear()
    return _db_instance


# Top-level helper functions delegating to active database instance
def get_item(pk: str, sk: str) -> Optional[Dict[str, Any]]:
    return get_db().get_item(pk, sk)


def put_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return get_db().put_item(item)


def put_item_if_absent(item: Dict[str, Any]) -> bool:
    return get_db().put_item(item, if_absent=True) is not None


def put_payment_and_receipt(payment: Dict[str, Any], receipt: Dict[str, Any]) -> bool:
    """Commit both financial records once for a given payment key."""
    return get_db().put_payment_and_receipt(payment, receipt)


def delete_item(pk: str, sk: str) -> bool:
    return get_db().delete_item(pk, sk)


def query_by_prefix(
    pk: str, sk_prefix: str, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    return get_db().query_by_prefix(pk, sk_prefix, limit)


def query_all_for_household(pk: str) -> List[Dict[str, Any]]:
    return get_db().query_pk(pk)


def update_item(
    pk: str, sk: str, updates: Dict[str, Any], *, expected_status: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    return get_db().update_item(pk, sk, updates, expected_status=expected_status)


def batch_write_items(items: List[Dict[str, Any]]) -> None:
    return get_db().batch_write_items(items)


# Entity-specific convenience queries
def get_household_rules(household_id: str) -> Optional[Dict[str, Any]]:
    pk = format_pk(household_id)
    # Check RULE#CONFIG first, fallback to RULE#FINANCIAL
    config = get_item(pk, f"{SKPrefix.RULE}CONFIG")
    financial = get_item(pk, f"{SKPrefix.RULE}FINANCIAL")
    if config and financial:
        merged = dict(financial)
        merged.update(config)
        return merged
    return config or financial


def get_household_config(household_id: str) -> Optional[Dict[str, Any]]:
    pk = format_pk(household_id)
    config = get_item(pk, f"{SKPrefix.RULE}CONFIG") or {}
    financial = get_item(pk, f"{SKPrefix.RULE}FINANCIAL") or {}
    merged = dict(financial)
    merged.update(config)
    if financial.get("frozen") is True or config.get("frozen") is True:
        merged["frozen"] = True
    return merged


def get_assessment(household_id: str, assessment_id: str) -> Optional[Dict[str, Any]]:
    pk = format_pk(household_id)
    if not assessment_id:
        return None
    clean_id = assessment_id
    for prefix in ("ASSESS#", "ASSESSMENT#"):
        if clean_id.startswith(prefix):
            clean_id = clean_id[len(prefix):]
            break
    return get_item(pk, f"ASSESS#{clean_id}") or get_item(pk, f"ASSESSMENT#{clean_id}")


def mark_assessment_consumed(
    household_id: str, assessment_id: str
) -> Optional[Dict[str, Any]]:
    from datetime import datetime, timezone
    pk = format_pk(household_id)
    clean_id = assessment_id
    for prefix in ("ASSESS#", "ASSESSMENT#"):
        if clean_id.startswith(prefix):
            clean_id = clean_id[len(prefix):]
            break
    now_iso = datetime.now(timezone.utc).isoformat()
    updates = {
        "used": True,
        "consumed": True,
        "status": "CONSUMED",
        "consumed_at": now_iso,
        "used_at": now_iso,
    }
    # Try updating ASSESS# first, then fallback to ASSESSMENT#
    updated = update_item(pk, f"ASSESS#{clean_id}", updates)
    if not updated:
        updated = update_item(pk, f"ASSESSMENT#{clean_id}", updates)
    return updated


def get_payment(household_id: str, idem_key: str) -> Optional[Dict[str, Any]]:
    pk = format_pk(household_id)
    sk = (
        idem_key
        if idem_key.startswith(SKPrefix.PAYMENT)
        else f"{SKPrefix.PAYMENT}{idem_key}"
    )
    return get_item(pk, sk)


def get_receipt(household_id: str, receipt_id: str) -> Optional[Dict[str, Any]]:
    pk = format_pk(household_id)
    sk = (
        receipt_id
        if receipt_id.startswith(SKPrefix.RECEIPT)
        else f"{SKPrefix.RECEIPT}{receipt_id}"
    )
    return get_item(pk, sk)


def get_household_members(household_id: str) -> List[Dict[str, Any]]:
    pk = format_pk(household_id)
    return query_by_prefix(pk, SKPrefix.MEMBER)


def get_billers(household_id: str) -> List[Dict[str, Any]]:
    pk = format_pk(household_id)
    return query_by_prefix(pk, SKPrefix.BILLER)


def get_payees(household_id: str) -> List[Dict[str, Any]]:
    pk = format_pk(household_id)
    return query_by_prefix(pk, SKPrefix.PAYEE)


def get_meds(household_id: str) -> List[Dict[str, Any]]:
    pk = format_pk(household_id)
    return query_by_prefix(pk, SKPrefix.MED)


def get_approval(household_id: str, approval_id: str) -> Optional[Dict[str, Any]]:
    pk = format_pk(household_id)
    sk = (
        approval_id
        if approval_id.startswith(SKPrefix.APPROVAL)
        else f"{SKPrefix.APPROVAL}{approval_id}"
    )
    return get_item(pk, sk)


def get_recent_audits(
    household_id: str, limit: int = 20
) -> List[Dict[str, Any]]:
    pk = format_pk(household_id)
    items = query_by_prefix(pk, SKPrefix.AUDIT)
    items.sort(key=lambda x: str(x.get("SK", "")), reverse=True)
    return items[:limit]


def get_recent_payments(
    household_id: str, limit: int = 10
) -> List[Dict[str, Any]]:
    pk = format_pk(household_id)
    items = query_by_prefix(pk, SKPrefix.PAYMENT)
    items.sort(key=lambda x: str(x.get("SK", "")), reverse=True)
    return items[:limit]
