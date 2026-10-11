import asyncio
import logging
from datetime import date, datetime
from functools import lru_cache

import holidays
from sqlalchemy.orm import Session

from app.config import (
    GOTIFY_CONFIGURED,
    HOLIDAY_COUNTRY,
    HOLIDAY_SUBDIV,
    REMINDER_CHECK_SECONDS,
    REMINDER_CUTOFF,
    REMINDER_TIME,
    REMINDER_WEEKDAYS,
)
from app.database import SessionLocal
from app.models import NotificationSettings, WorkSession
from app.schemas import NotificationSettingsResponse
from app.services import gotify
from app.services.timer import get_or_create_timer_state, timer_transaction

logger = logging.getLogger(__name__)

START_REMINDER_TITLE = "TickTick: Zeiterfassung starten?"
START_REMINDER_MESSAGE = "Heute wurde noch keine Arbeitszeit erfasst. Hast du vergessen, den Timer zu starten?"
TEST_TITLE = "TickTick: Testbenachrichtigung"
TEST_MESSAGE = "Gotify ist korrekt eingerichtet."


def get_or_create_settings(db: Session) -> NotificationSettings:
    settings = db.get(NotificationSettings, 1)
    if settings is None:
        settings = NotificationSettings(id=1, enabled=True)
        db.add(settings)
        db.flush()
    return settings


def normalize_pause(settings: NotificationSettings, today: date) -> None:
    """Clear expired pauses so stale dates never block later updates."""
    if settings.paused_until is not None and settings.paused_until < today:
        settings.paused_until = None


@lru_cache(maxsize=1)
def _holiday_calendar() -> holidays.HolidayBase | None:
    if not HOLIDAY_COUNTRY:
        return None
    # Years expand lazily on lookup; German names match the UI language.
    return holidays.country_holidays(
        HOLIDAY_COUNTRY, subdiv=HOLIDAY_SUBDIV, language="de"
    )


def holiday_name(day: date) -> str | None:
    calendar = _holiday_calendar()
    if calendar is None:
        return None
    return calendar.get(day)


def is_workday(day: date) -> bool:
    return day.weekday() in REMINDER_WEEKDAYS and holiday_name(day) is None


def start_reminder_skip_reason(
    settings: NotificationSettings,
    now: datetime,
    has_day_session: bool,
    timer_idle: bool,
) -> str | None:
    """Return why no start reminder is due now, or None when it should be sent."""
    today = now.date()
    if not settings.enabled:
        return "disabled"
    if settings.paused_until is not None and today <= settings.paused_until:
        return "paused"
    if not is_workday(today):
        return "not_workday"
    if now.time() < REMINDER_TIME:
        return "too_early"
    if now.time() >= REMINDER_CUTOFF:
        return "past_cutoff"
    if settings.last_start_reminder_date == today:
        return "already_sent"
    if not timer_idle:
        return "timer_active"
    if has_day_session:
        return "session_exists"
    return None


def _has_day_session(db: Session, day: date) -> bool:
    # Reset sessions were discarded by the user and do not count as started work.
    return (
        db.query(WorkSession.id)
        .filter(WorkSession.date == day, WorkSession.status != "reset")
        .first()
        is not None
    )


def _claim_start_reminder(db: Session, now: datetime) -> tuple[bool, date | None]:
    """Claim today's reminder. Returns (claimed, previous claim date)."""
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        normalize_pause(settings, now.date())
        state = get_or_create_timer_state(db)
        reason = start_reminder_skip_reason(
            settings,
            now,
            has_day_session=_has_day_session(db, now.date()),
            timer_idle=state.current_session_id is None,
        )
        if reason is not None:
            logger.debug("Start reminder skipped", extra={"reason": reason})
            return False, None
        previous = settings.last_start_reminder_date
        settings.last_start_reminder_date = now.date()
        db.flush()
        return True, previous


def _release_start_reminder(db: Session, today: date, previous: date | None) -> None:
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        # Compare-and-set: only undo our own claim.
        if settings.last_start_reminder_date == today:
            settings.last_start_reminder_date = previous
            db.flush()


def check_start_reminder(db: Session, now: datetime) -> bool:
    """Send the daily start reminder if due. Returns True when a message was sent.

    At-most-once: the day is claimed and committed before sending, outside the
    SQLite write lock. Only definite non-delivery releases the claim for retry.
    """
    claimed, previous = _claim_start_reminder(db, now)
    if not claimed:
        return False
    try:
        gotify.send_message(START_REMINDER_TITLE, START_REMINDER_MESSAGE)
    except gotify.GotifyError as error:
        if error.retryable:
            _release_start_reminder(db, now.date(), previous)
            logger.warning(
                "Start reminder not delivered; will retry next check",
                extra={"error": str(error), "day": str(now.date())},
            )
        else:
            logger.error(
                "Start reminder may not have been delivered; not retrying to avoid duplicates",
                extra={"error": str(error), "day": str(now.date())},
            )
        return False
    logger.info("Start reminder sent", extra={"day": str(now.date())})
    return True


def _check_once() -> None:
    db = SessionLocal()
    try:
        check_start_reminder(db, datetime.now())
    finally:
        db.close()


async def run_reminder_loop() -> None:
    logger.info(
        "Start reminder loop running",
        extra={
            "reminder_time": REMINDER_TIME.strftime("%H:%M"),
            "reminder_cutoff": REMINDER_CUTOFF.strftime("%H:%M"),
            "check_seconds": REMINDER_CHECK_SECONDS,
        },
    )
    while True:
        try:
            await asyncio.to_thread(_check_once)
        except Exception:
            logger.exception("Start reminder check failed")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


def _settings_response(
    settings: NotificationSettings, today: date
) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        configured=GOTIFY_CONFIGURED,
        enabled=settings.enabled,
        paused_until=settings.paused_until,
        reminder_time=REMINDER_TIME.strftime("%H:%M"),
        reminder_cutoff=REMINDER_CUTOFF.strftime("%H:%M"),
        today_is_workday=is_workday(today),
        today_holiday=holiday_name(today),
    )


def get_settings(db: Session, now: datetime) -> NotificationSettingsResponse:
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        normalize_pause(settings, now.date())
        db.flush()
        return _settings_response(settings, now.date())


def update_settings(
    db: Session, enabled: bool, paused_until: date | None, now: datetime
) -> NotificationSettingsResponse:
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        settings.enabled = enabled
        settings.paused_until = paused_until
        normalize_pause(settings, now.date())
        db.flush()
        logger.info(
            "Notification settings updated",
            extra={"enabled": enabled, "paused_until": str(settings.paused_until)},
        )
        return _settings_response(settings, now.date())


def send_test_message() -> None:
    gotify.send_message(TEST_TITLE, TEST_MESSAGE)
