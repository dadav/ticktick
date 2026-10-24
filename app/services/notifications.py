import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

import holidays
from sqlalchemy.orm import Session

from app.config import (
    DAILY_REQUIREMENT_MINUTES,
    GOTIFY_CONFIGURED,
    HOLIDAY_COUNTRY,
    HOLIDAY_SUBDIV,
    MAX_DAILY_HOURS,
    REMINDER_CHECK_SECONDS,
    REMINDER_CUTOFF,
    REMINDER_TIME,
    REMINDER_WEEKDAYS,
)
from app.database import SessionLocal
from app.models import (
    MilestoneEvent,
    MilestoneSettings,
    NotificationSettings,
    WorkSession,
)
from app.schemas import DailySummary, NotificationSettingsResponse
from app.services import gotify
from app.services.calculations import MIN_WORK_HOURS, calculate_day, format_hours_label
from app.services.timer import (
    enforce_daily_cap,
    get_active_session,
    get_or_create_timer_state,
    timer_transaction,
)
from app.services.workdays import load_day_sessions

logger = logging.getLogger(__name__)

START_REMINDER_TITLE = "TickTick: Zeiterfassung starten?"
START_REMINDER_MESSAGE = "Heute wurde noch keine Arbeitszeit erfasst. Hast du vergessen, den Timer zu starten?"
TEST_TITLE = "TickTick: Testbenachrichtigung"
TEST_MESSAGE = "Gotify ist korrekt eingerichtet."

MILESTONE_KEYS = ("min_work", "daily_target", "daily_max")
# A reached milestone is only sent while its workday saw activity this recently.
MILESTONE_FRESH_SECONDS = 30 * 60
MILESTONE_RETENTION_DAYS = 7


@dataclass(frozen=True)
class PendingMilestone:
    work_date: date
    key: str
    day_end: datetime  # Last session end of the workday, or now while active


def get_or_create_settings(db: Session) -> NotificationSettings:
    settings = db.get(NotificationSettings, 1)
    if settings is None:
        settings = NotificationSettings(id=1, enabled=True)
        db.add(settings)
        db.flush()
    return settings


def get_or_create_milestone_settings(db: Session) -> MilestoneSettings:
    settings = db.get(MilestoneSettings, 1)
    if settings is None:
        settings = MilestoneSettings(
            id=1,
            notify_min_work=False,
            notify_daily_target=False,
            notify_daily_max=False,
        )
        db.add(settings)
        db.flush()
    return settings


def normalize_pause(settings: NotificationSettings, today: date) -> None:
    """Clear expired pauses so stale dates never block later updates."""
    if settings.paused_until is not None and settings.paused_until < today:
        settings.paused_until = None


def is_paused(settings: NotificationSettings, today: date) -> bool:
    """The pause date mutes the start reminder and all milestones (inclusive)."""
    return settings.paused_until is not None and today <= settings.paused_until


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
    if is_paused(settings, today):
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


def reached_milestones(day: DailySummary | None) -> list[str]:
    """Milestones reached by a workday, in MILESTONE_KEYS order.

    min_work uses actual work: without a break, credited work drops by the lunch
    deduction right after the threshold, so a periodic check could miss it.
    daily_target and daily_max use credited work, so a missing lunch break moves
    them later (8h12 becomes 8h42, 10h becomes 10h30).
    """
    if day is None:
        return []
    reached = {
        "min_work": day.actual_work_seconds >= MIN_WORK_HOURS * 3600,
        "daily_target": day.target_reached,
        "daily_max": day.cap_reached,
    }
    return [key for key in MILESTONE_KEYS if reached[key]]


def milestone_enabled(settings: MilestoneSettings, key: str) -> bool:
    flags = {
        "min_work": settings.notify_min_work,
        "daily_target": settings.notify_daily_target,
        "daily_max": settings.notify_daily_max,
    }
    return flags[key]


def classify_milestone(
    milestone: PendingMilestone,
    settings: NotificationSettings,
    milestone_settings: MilestoneSettings,
    now: datetime,
) -> str:
    """Return "due" or the outcome recorded instead of sending."""
    if (now - milestone.day_end).total_seconds() > MILESTONE_FRESH_SECONDS:
        return "stale"
    if is_paused(settings, now.date()):
        return "paused"
    if not milestone_enabled(milestone_settings, milestone.key):
        return "disabled"
    return "due"


def milestone_message(key: str, work_date: date, today: date) -> tuple[str, str]:
    messages = {
        "min_work": (
            f"TickTick: {format_hours_label(MIN_WORK_HOURS * 60)} erreicht",
            "Frühester Feierabend möglich.",
        ),
        "daily_target": (
            "TickTick: Tagessoll erreicht",
            f"{format_hours_label(DAILY_REQUIREMENT_MINUTES)} anrechenbare "
            "Arbeitszeit erreicht. Regulärer Feierabend möglich.",
        ),
        # Generic on purpose: idle history corrections can reach the cap as well.
        "daily_max": (
            "TickTick: Tagesmaximum erreicht",
            f"{format_hours_label(MAX_DAILY_HOURS * 60)} anrechenbare Arbeitszeit "
            "erreicht. Eine laufende Erfassung wird automatisch beendet.",
        ),
    }
    title, message = messages[key]
    if work_date != today:
        message = f"{message} (Arbeitstag {work_date:%d.%m.%Y})"
    return title, message


def _candidate_dates(now: datetime, active_date: date | None) -> list[date]:
    # Yesterday covers overnight sessions that stopped after midnight.
    dates = {now.date(), now.date() - timedelta(days=1)}
    if active_date is not None:
        dates.add(active_date)
    return sorted(dates)


def _pending_milestones(
    db: Session, now: datetime, active_date: date | None
) -> list[PendingMilestone]:
    """Reached milestones of candidate workdays that have no event row yet."""
    pending = []
    for work_date in _candidate_dates(now, active_date):
        day = calculate_day(load_day_sessions(db, work_date, include_active=True), now)
        if day is None:
            continue
        handled = {
            row.key
            for row in db.query(MilestoneEvent.key).filter(
                MilestoneEvent.work_date == work_date
            )
        }
        for key in reached_milestones(day):
            if key not in handled:
                pending.append(PendingMilestone(work_date, key, day.end_time))
    return pending


def _record_milestone(
    db: Session, milestone: PendingMilestone, outcome: str, now: datetime
) -> None:
    db.add(
        MilestoneEvent(
            work_date=milestone.work_date,
            key=milestone.key,
            outcome=outcome,
            recorded_at=now,
        )
    )


def _prune_milestone_events(db: Session, today: date) -> None:
    cutoff = today - timedelta(days=MILESTONE_RETENTION_DAYS)
    db.query(MilestoneEvent).filter(MilestoneEvent.work_date < cutoff).delete(
        synchronize_session=False
    )


def _record_suppressed_milestones(
    db: Session,
    settings: NotificationSettings,
    milestone_settings: MilestoneSettings,
    now: datetime,
) -> int:
    """Mark milestones reached under the current suppression as handled.

    Runs before a settings change in the same transaction, so enabling a
    milestone or clearing a pause never sends one that passed while suppressed.
    Due milestones stay pending; they passed while enabled and the loop sends them.
    """
    active = get_active_session(db)
    count = 0
    for milestone in _pending_milestones(db, now, active.date if active else None):
        if classify_milestone(milestone, settings, milestone_settings, now) != "due":
            _record_milestone(db, milestone, "baseline", now)
            count += 1
    return count


def _claim_milestones(db: Session, now: datetime) -> list[PendingMilestone]:
    """Enforce the cap, then record every pending milestone. Returns those to send."""
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        normalize_pause(settings, now.date())
        milestone_settings = get_or_create_milestone_settings(db)
        state = get_or_create_timer_state(db)
        active = get_active_session(db)
        # Captured before enforcement so an overnight session stopped here still counts.
        active_date = active.date if active else None
        if active is not None:
            enforce_daily_cap(db, active, state, now)
        _prune_milestone_events(db, now.date())
        due = []
        for milestone in _pending_milestones(db, now, active_date):
            outcome = classify_milestone(milestone, settings, milestone_settings, now)
            if outcome == "due":
                _record_milestone(db, milestone, "claimed", now)
                due.append(milestone)
                continue
            _record_milestone(db, milestone, outcome, now)
            logger.debug(
                "Milestone notification skipped",
                extra={
                    "key": milestone.key,
                    "work_date": str(milestone.work_date),
                    "reason": outcome,
                },
            )
        db.flush()
        return due


def _release_milestone(db: Session, milestone: PendingMilestone) -> None:
    with timer_transaction(db):
        # Compare-and-set: only undo our own claim.
        db.query(MilestoneEvent).filter(
            MilestoneEvent.work_date == milestone.work_date,
            MilestoneEvent.key == milestone.key,
            MilestoneEvent.outcome == "claimed",
        ).delete(synchronize_session=False)


def check_milestones(db: Session, now: datetime) -> list[PendingMilestone]:
    """Send newly reached milestone notifications. Returns the delivered ones.

    At-most-once per workday and milestone: the event row is committed before
    sending. Only definite non-delivery deletes it for a retry on the next check.
    """
    sent = []
    for milestone in _claim_milestones(db, now):
        log_extra = {"key": milestone.key, "work_date": str(milestone.work_date)}
        title, message = milestone_message(
            milestone.key, milestone.work_date, now.date()
        )
        try:
            gotify.send_message(title, message)
        except gotify.GotifyError as error:
            if error.retryable:
                _release_milestone(db, milestone)
                logger.warning(
                    "Milestone notification not delivered; will retry next check",
                    extra={**log_extra, "error": str(error)},
                )
            else:
                logger.error(
                    "Milestone notification may not have been delivered; not retrying to avoid duplicates",
                    extra={**log_extra, "error": str(error)},
                )
            continue
        logger.info("Milestone notification sent", extra=log_extra)
        sent.append(milestone)
    return sent


def _check_once() -> None:
    # Separate sessions and error boundaries: one failing check never blocks the other.
    db = SessionLocal()
    try:
        check_start_reminder(db, datetime.now())
    except Exception:
        logger.exception("Start reminder check failed")
    finally:
        db.close()
    db = SessionLocal()
    try:
        check_milestones(db, datetime.now())
    except Exception:
        logger.exception("Milestone check failed")
    finally:
        db.close()


async def run_reminder_loop() -> None:
    logger.info(
        "Notification loop running",
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
            logger.exception("Notification check failed")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


def _settings_response(
    settings: NotificationSettings,
    milestone_settings: MilestoneSettings,
    today: date,
) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        configured=GOTIFY_CONFIGURED,
        enabled=settings.enabled,
        paused_until=settings.paused_until,
        reminder_time=REMINDER_TIME.strftime("%H:%M"),
        reminder_cutoff=REMINDER_CUTOFF.strftime("%H:%M"),
        today_is_workday=is_workday(today),
        today_holiday=holiday_name(today),
        notify_min_work=milestone_settings.notify_min_work,
        notify_daily_target=milestone_settings.notify_daily_target,
        notify_daily_max=milestone_settings.notify_daily_max,
    )


def get_settings(db: Session, now: datetime) -> NotificationSettingsResponse:
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        normalize_pause(settings, now.date())
        milestone_settings = get_or_create_milestone_settings(db)
        db.flush()
        return _settings_response(settings, milestone_settings, now.date())


def update_settings(
    db: Session,
    enabled: bool,
    paused_until: date | None,
    now: datetime,
    notify_min_work: bool | None = None,
    notify_daily_target: bool | None = None,
    notify_daily_max: bool | None = None,
) -> NotificationSettingsResponse:
    """Apply settings; None milestone flags keep their stored value."""
    with timer_transaction(db):
        settings = get_or_create_settings(db)
        normalize_pause(settings, now.date())
        milestone_settings = get_or_create_milestone_settings(db)
        baseline = _record_suppressed_milestones(db, settings, milestone_settings, now)
        settings.enabled = enabled
        settings.paused_until = paused_until
        normalize_pause(settings, now.date())
        if notify_min_work is not None:
            milestone_settings.notify_min_work = notify_min_work
        if notify_daily_target is not None:
            milestone_settings.notify_daily_target = notify_daily_target
        if notify_daily_max is not None:
            milestone_settings.notify_daily_max = notify_daily_max
        db.flush()
        logger.info(
            "Notification settings updated",
            extra={
                "enabled": enabled,
                "paused_until": str(settings.paused_until),
                "notify_min_work": milestone_settings.notify_min_work,
                "notify_daily_target": milestone_settings.notify_daily_target,
                "notify_daily_max": milestone_settings.notify_daily_max,
                "baseline_milestones": baseline,
            },
        )
        return _settings_response(settings, milestone_settings, now.date())


def send_test_message() -> None:
    gotify.send_message(TEST_TITLE, TEST_MESSAGE)
