"""Configuration module for BETA backend.

Loads environment variables with sensible defaults for AWS Bedrock AgentCore
Gateway, DynamoDB, Twilio WhatsApp, Google Calendar, and security rules.
"""

import os
from pathlib import Path
from typing import Optional

# Optional dotenv loading if python-dotenv is installed
try:
    from dotenv import load_dotenv

    # Search for .env in current directory, backend/, or parent directory
    base_dir = Path(__file__).resolve().parent.parent
    for env_path in [base_dir / ".env", base_dir.parent / ".env", Path(".env")]:
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path)
            break
except ImportError:
    pass


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "t", "y")


def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


class Settings:
    """Application settings and environment configuration."""

    # AWS Configuration (region ap-south-1 for Mumbai, India)
    AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")
    DYNAMODB_TABLE: str = os.getenv("DYNAMODB_TABLE", "BETA_Main")

    # Local Mock DB mode (run offline without AWS credentials)
    LOCAL_MOCK_DB: bool = _get_bool("LOCAL_MOCK_DB", True)
    MOCK_DB_FILE: str = os.getenv(
        "MOCK_DB_FILE",
        str(Path(__file__).resolve().parent.parent / ".mock_db.json"),
    )

    # Core Security & Approval Timing
    APPROVAL_TTL_SECONDS: int = _get_int("APPROVAL_TTL_SECONDS", 7200)  # 2 hours
    COOLING_OFF_SECONDS: int = _get_int(
        "COOLING_OFF_SECONDS", 20
    )  # 20s demo default, 1800s prod
    DEFAULT_HOUSEHOLD_ID: str = os.getenv("DEFAULT_HOUSEHOLD_ID", "HH#KAMALA_01")

    # Twilio WhatsApp Credentials
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_FROM: str = os.getenv(
        "TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"
    )
    GUARDIAN_WHATSAPP_TO: str = os.getenv("GUARDIAN_WHATSAPP_TO", "")

    # Google Calendar Integration
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON", "{}"
    )

    # Cedar Policies
    CEDAR_POLICY_PATH: str = os.getenv(
        "CEDAR_POLICY_PATH",
        str(
            Path(__file__).resolve().parent.parent
            / "policies"
            / "beta_policies.cedar"
        ),
    )


settings = Settings()

# Convenient top-level exports for direct import
AWS_REGION = settings.AWS_REGION
DYNAMODB_TABLE = settings.DYNAMODB_TABLE
LOCAL_MOCK_DB = settings.LOCAL_MOCK_DB
MOCK_DB_FILE = settings.MOCK_DB_FILE
APPROVAL_TTL_SECONDS = settings.APPROVAL_TTL_SECONDS
COOLING_OFF_SECONDS = settings.COOLING_OFF_SECONDS
DEFAULT_HOUSEHOLD_ID = settings.DEFAULT_HOUSEHOLD_ID
TWILIO_ACCOUNT_SID = settings.TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN = settings.TWILIO_AUTH_TOKEN
TWILIO_WHATSAPP_FROM = settings.TWILIO_WHATSAPP_FROM
GUARDIAN_WHATSAPP_TO = settings.GUARDIAN_WHATSAPP_TO
GOOGLE_CALENDAR_ID = settings.GOOGLE_CALENDAR_ID
GOOGLE_SERVICE_ACCOUNT_JSON = settings.GOOGLE_SERVICE_ACCOUNT_JSON
CEDAR_POLICY_PATH = settings.CEDAR_POLICY_PATH
