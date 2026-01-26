from datetime import datetime, timedelta

from app.config import (
    DAILY_REQUIREMENT_MINUTES,
    MAX_DAILY_HOURS,
    LUNCH_THRESHOLD_HOURS,
    LUNCH_DURATION_MINUTES,
)
from app.models import WorkSession

# Minimum hours before you can leave (6 hours)
MIN_WORK_HOURS = 6


def format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS"""
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    sign = "-" if seconds < 0 else ""
    return f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def format_duration_short(seconds: int) -> str:
    """Format seconds as HH:MM (without seconds)"""
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, _ = divmod(remainder, 60)
    sign = "-" if seconds < 0 else ""
    return f"{sign}{int(hours):02d}:{int(minutes):02d}"


def format_time(dt: datetime) -> str:
    """Format datetime as HH:MM"""
    return dt.strftime("%H:%M")


def calculate_pause_seconds(session: WorkSession, now: datetime | None = None) -> int:
    """Calculate total pause time in seconds for a session"""
    if now is None:
        now = datetime.now()

    total_pause = 0
    for pause in session.pause_periods:
        pause_end = pause.pause_end or now
        total_pause += int((pause_end - pause.pause_start).total_seconds())
    return total_pause


def calculate_net_work_seconds(session: WorkSession, now: datetime | None = None) -> int:
    """Calculate net work time in seconds (elapsed - pauses)"""
    if now is None:
        now = datetime.now()

    if session.end_time:
        elapsed = (session.end_time - session.start_time).total_seconds()
    else:
        elapsed = (now - session.start_time).total_seconds()

    pause_seconds = calculate_pause_seconds(session, now)
    return max(0, int(elapsed - pause_seconds))


def calculate_lunch_break_minutes(work_minutes: float) -> int:
    """Return lunch break duration if working more than threshold"""
    if work_minutes > LUNCH_THRESHOLD_HOURS * 60:
        return LUNCH_DURATION_MINUTES
    return 0


def calculate_earliest_leave(start_time: datetime, pause_minutes: int) -> datetime:
    """
    Calculate earliest possible leave time (minimum 6 hours work).
    earliest_leave = start_time + 6h + pause_time
    No lunch break at 6h since it's exactly at threshold.
    """
    work_minutes = MIN_WORK_HOURS * 60
    total_minutes = work_minutes + pause_minutes
    return start_time + timedelta(minutes=total_minutes)


def calculate_normal_leave(start_time: datetime, pause_minutes: int) -> datetime:
    """
    Calculate normal leave time (daily requirement of 8h 12m).
    normal_leave = start_time + 8h12m + pause_time + lunch_break
    """
    work_minutes = DAILY_REQUIREMENT_MINUTES
    lunch_break = LUNCH_DURATION_MINUTES  # Always applies since 8h12m > 6h
    total_minutes = work_minutes + pause_minutes + lunch_break
    return start_time + timedelta(minutes=total_minutes)


def calculate_latest_leave(start_time: datetime, pause_minutes: int) -> datetime:
    """
    Calculate maximum allowed leave time (10 hour work day limit).
    latest_leave = start_time + 10h + pause_time + lunch_break
    """
    work_minutes = MAX_DAILY_HOURS * 60
    lunch_break = LUNCH_DURATION_MINUTES
    total_minutes = work_minutes + pause_minutes + lunch_break
    return start_time + timedelta(minutes=total_minutes)


def calculate_lunch_break_time(start_time: datetime, pause_minutes: int) -> datetime:
    """Calculate when lunch break starts being applied"""
    threshold_minutes = LUNCH_THRESHOLD_HOURS * 60 + pause_minutes
    return start_time + timedelta(minutes=threshold_minutes)


def calculate_remaining_for_daily(net_work_seconds: int) -> int:
    """Calculate seconds remaining to reach daily requirement"""
    target_seconds = int(DAILY_REQUIREMENT_MINUTES * 60)
    return max(0, target_seconds - net_work_seconds)


def calculate_overtime_seconds(net_work_seconds: int) -> int:
    """Calculate overtime (positive) or undertime (negative) for the day"""
    target_seconds = int(DAILY_REQUIREMENT_MINUTES * 60)
    return net_work_seconds - target_seconds
