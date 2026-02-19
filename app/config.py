import os
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
