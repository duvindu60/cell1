"""Business-local time for cell meeting and attendance rules (default: Asia/Colombo)."""
import os
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

DEFAULT_APP_TIMEZONE = "Asia/Colombo"


@lru_cache(maxsize=1)
def get_app_tz() -> ZoneInfo:
    name = os.getenv("APP_TIMEZONE", DEFAULT_APP_TIMEZONE).strip() or DEFAULT_APP_TIMEZONE
    return ZoneInfo(name)


def app_now() -> datetime:
    """Current time in the app business timezone."""
    return datetime.now(get_app_tz())


def app_today() -> date:
    """Current calendar date in the app business timezone."""
    return app_now().date()
