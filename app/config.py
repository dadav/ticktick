import os
from datetime import datetime, time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = os.getenv("TICKTICK_DB_PATH", str(BASE_DIR / "data" / "ticktick.db"))
WEEKLY_HOURS = float(os.getenv("TICKTICK_WEEKLY_HOURS", "41"))
MAX_DAILY_HOURS = float(os.getenv("TICKTICK_MAX_DAILY_HOURS", "10"))
LUNCH_THRESHOLD_HOURS = float(os.getenv("TICKTICK_LUNCH_THRESHOLD", "6"))
LUNCH_DURATION_MINUTES = int(os.getenv("TICKTICK_LUNCH_DURATION", "30"))
HOST = os.getenv("TICKTICK_HOST", "0.0.0.0")
PORT = int(os.getenv("TICKTICK_PORT", "8000"))

# Calculated values
WORK_DAYS_PER_WEEK = 5
DAILY_REQUIREMENT_MINUTES = (WEEKLY_HOURS * 60) / WORK_DAYS_PER_WEEK
MAX_DAILY_SECONDS = int(MAX_DAILY_HOURS * 3600)


def _parse_clock(name: str, default: str) -> time:
    raw = os.getenv(name, default).strip()
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError as error:
        raise ValueError(f"{name} must use HH:MM format, got {raw!r}") from error


def _parse_weekdays(name: str, default: str) -> frozenset[int]:
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    raw = os.getenv(name, default)
    days = set()
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        if item not in names:
            raise ValueError(f"{name} contains unknown weekday {item!r}; use {names}")
        days.add(names.index(item))
    return frozenset(days)


# Gotify push notifications; reminders stay off unless URL and token are set.
GOTIFY_URL = os.getenv("TICKTICK_GOTIFY_URL", "").strip().rstrip("/")
GOTIFY_TOKEN = os.getenv("TICKTICK_GOTIFY_TOKEN", "").strip()
GOTIFY_PRIORITY = int(os.getenv("TICKTICK_GOTIFY_PRIORITY", "5"))
GOTIFY_CONFIGURED = bool(GOTIFY_URL and GOTIFY_TOKEN)

# Start reminder window in local time: due from REMINDER_TIME until before REMINDER_CUTOFF.
REMINDER_TIME = _parse_clock("TICKTICK_REMINDER_TIME", "09:00")
REMINDER_CUTOFF = _parse_clock("TICKTICK_REMINDER_CUTOFF", "12:00")
if REMINDER_CUTOFF <= REMINDER_TIME:
    raise ValueError(
        "TICKTICK_REMINDER_CUTOFF must be later than TICKTICK_REMINDER_TIME, "
        f"got {REMINDER_CUTOFF:%H:%M} <= {REMINDER_TIME:%H:%M}"
    )
REMINDER_WEEKDAYS = _parse_weekdays("TICKTICK_REMINDER_WEEKDAYS", "mon,tue,wed,thu,fri")
HOLIDAY_COUNTRY = os.getenv("TICKTICK_HOLIDAY_COUNTRY", "DE").strip().upper()
HOLIDAY_SUBDIV = os.getenv("TICKTICK_HOLIDAY_SUBDIV", "").strip().upper() or None
REMINDER_CHECK_SECONDS = 60
