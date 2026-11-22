"""Create DynamoDB Single-Table schema for BETA (BETA_Household).

Creates table with:
- PK: String (Partition Key)
- SK: String (Sort Key)
- BillingMode: PAY_PER_REQUEST (On-Demand)
- TTL enabled on attribute 'ttl'
- Graceful offline fallback if LOCAL_MOCK_DB=true.
"""

import logging
import os
import sys
from pathlib import Path

# Add backend directory to path if running directly
current_dir = Path(__file__).resolve().parent
backend_dir = current_dir.parent
project_dir = backend_dir.parent
for p in [str(project_dir), str(backend_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.common.config import settings
from backend.common.db import get_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def create_table() -> bool:
    """Create DynamoDB table with single-table design and TTL enabled."""
    table_name = settings.DYNAMODB_TABLE
    region = settings.AWS_REGION
    use_mock = settings.LOCAL_MOCK_DB

    logger.info(
        f"Initializing table '{table_name}' in region '{region}' (LOCAL_MOCK_DB={use_mock})..."
    )

    if use_mock:
        logger.info(
            f"LOCAL_MOCK_DB is active. Initializing offline mock store at: {settings.MOCK_DB_FILE}"
        )
        db = get_db(force_mock=True)
        # Ensure persistence directory exists
        mock_file = Path(settings.MOCK_DB_FILE)
        mock_file.parent.mkdir(parents=True, exist_ok=True)
        if not mock_file.exists():
            with open(mock_file, "w", encoding="utf-8") as f:
                f.write("{}")
        logger.info(
            f"✅ Mock table '{table_name}' initialized successfully (offline mode)."
        )
        return True

    # Real AWS DynamoDB creation
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        logger.error(
            "boto3 is required to create real DynamoDB table. Install boto3 or set LOCAL_MOCK_DB=true."
        )
        return False

    client = boto3.client("dynamodb", region_name=region)

    try:
        # Check if table already exists
        client.describe_table(TableName=table_name)
        logger.info(f"Table '{table_name}' already exists in {region}.")
    except client.exceptions.ResourceNotFoundException:
        logger.info(f"Creating table '{table_name}'...")
        client.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            Tags=[
                {"Key": "Project", "Value": "BETA"},
                {"Key": "Environment", "Value": "Production"},
                {"Key": "Region", "Value": "ap-south-1"},
            ],
        )
        logger.info(f"Waiting for table '{table_name}' to become ACTIVE...")
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        logger.info(f"Table '{table_name}' is now ACTIVE.")

    # Enable TTL on 'ttl' attribute
    try:
        ttl_desc = client.describe_time_to_live(TableName=table_name)
        status = ttl_desc.get("TimeToLiveDescription", {}).get(
            "TimeToLiveStatus"
        )
        if status in ("ENABLED", "ENABLING"):
            logger.info("TTL is already enabled on attribute 'ttl'.")
        else:
            logger.info("Enabling TTL on attribute 'ttl'...")
            client.update_time_to_live(
                TableName=table_name,
                TimeToLiveSpecification={
                    "Enabled": True,
                    "AttributeName": "ttl",
                },
            )
            logger.info("TTL enabled successfully.")
    except ClientError as e:
        logger.warning(f"Could not update TTL configuration: {e}")

    logger.info(f"✅ DynamoDB Table '{table_name}' ready in {region}.")
    return True


if __name__ == "__main__":
    success = create_table()
    sys.exit(0 if success else 1)
