"""Google Calendar Integration Client for BETA.

Uses google-api-python-client with service account credentials from GOOGLE_SERVICE_ACCOUNT_JSON.
Includes graceful offline mock fallback when credentials are absent or invalid.
"""

from datetime import datetime, timezone, timedelta
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple
import uuid

from backend.common.config import settings

logger = logging.getLogger(__name__)

# Google client imports
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    service_account = None
    build = None
    HttpError = Exception


def _get_service_account_info() -> Optional[Dict[str, Any]]:
    """Parse service account JSON from env string or file path."""
    raw = (settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw or raw == "{}":
        return None

    # Check if raw is a file path
    if os.path.isfile(raw):
        try:
            with open(raw, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read service account JSON file: {e}")
            return None

    # Attempt parsing as JSON string
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON or file path.")
        return None


def create_calendar_event(
    summary: str,
    description: str,
    start_iso: str,
    duration_minutes: int = 30,
    calendar_id: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Create a Google Calendar event.

    Args:
        summary: Event title (e.g. 'Appointment with Dr. Rao')
        description: Event description
        start_iso: ISO 8601 start time string
        duration_minutes: Duration in minutes (default 30)
        calendar_id: Calendar ID (defaults to settings.GOOGLE_CALENDAR_ID)

    Returns:
        (is_real_google_event: bool, event_id: str, event_data: dict)
    """
    cal_id = calendar_id or settings.GOOGLE_CALENDAR_ID or "primary"

    # Compute start and end times
    try:
        clean_start = start_iso.replace("Z", "+00:00")
        start_dt = datetime.fromisoformat(clean_start)
    except Exception:
        start_dt = datetime.now(timezone.utc)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    event_body = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "Asia/Kolkata",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "Asia/Kolkata",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},
                {"method": "popup", "minutes": 1440},  # 1 day before
            ],
        },
    }

    sa_info = _get_service_account_info()
    if GOOGLE_AVAILABLE and sa_info and "private_key" in sa_info:
        try:
            scopes = ["https://www.googleapis.com/auth/calendar"]
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=scopes
            )
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            created_event = (
                service.events().insert(calendarId=cal_id, body=event_body).execute()
            )
            event_id = created_event.get("id", f"GCAL_{uuid.uuid4().hex[:10].upper()}")
            logger.info(f"Google Calendar event created successfully: ID {event_id}")
            return True, event_id, created_event
        except Exception as e:
            logger.warning(
                f"Google Calendar API invocation failed, falling back to mock: {e}"
            )

    # Local mock event fallback
    event_id = f"GCAL_MOCK_{uuid.uuid4().hex[:10].upper()}"
    event_body["id"] = event_id
    event_body["status"] = "confirmed"
    logger.info(f"[MOCK GCAL EVENT] ID={event_id} | Title='{summary}' | Time={start_iso}")
    return False, event_id, event_body
