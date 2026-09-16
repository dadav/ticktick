from datetime import datetime, timedelta
from math import ceil

from app.config import (
    DAILY_REQUIREMENT_MINUTES,
    MAX_DAILY_SECONDS,
    LUNCH_THRESHOLD_HOURS,
    LUNCH_DURATION_MINUTES,
)
from app.models import WorkSession
from app.schemas import DailySummary

MIN_WORK_HOURS = 6
Interval = tuple[datetime, datetime]


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    sign = "-" if seconds < 0 else ""
    return f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def format_duration_short(seconds: int) -> str:
    return format_duration(seconds)[:-3]


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def interval_gaps(
    start: datetime, end: datetime, intervals: list[Interval]
) -> list[Interval]:
    gaps = []
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_start > cursor:
            gaps.append((cursor, interval_start))
        cursor = max(cursor, interval_end)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def interval_seconds(intervals: list[Interval]) -> float:
    return sum((end - start).total_seconds() for start, end in intervals)


def session_work_intervals(session: WorkSession, now: datetime) -> list[Interval]:
    end = session.end_time or now
    pauses = merge_intervals(
        [
            (
                max(session.start_time, pause.pause_start),
                min(end, pause.pause_end or end),
            )
            for pause in session.pause_periods
        ]
    )
    return interval_gaps(session.start_time, end, pauses)


def calculate_net_work_seconds(
    session: WorkSession, now: datetime | None = None
) -> int:
    """Actual work is never capped or reduced by the automatic lunch deduction."""
    return int(interval_seconds(session_work_intervals(session, now or datetime.now())))


def calculate_pause_seconds(session: WorkSession, now: datetime | None = None) -> int:
    end = session.end_time or now or datetime.now()
    elapsed = max(0, (end - session.start_time).total_seconds())
    return int(elapsed - interval_seconds(session_work_intervals(session, end)))


def credited_seconds(work_seconds: float, break_seconds: float) -> float:
    deduction = 0
    if work_seconds > LUNCH_THRESHOLD_HOURS * 3600:
        deduction = max(0, LUNCH_DURATION_MINUTES * 60 - break_seconds)
    return max(0, work_seconds - deduction)


def remaining_work_seconds(
    work_seconds: float, break_seconds: float, target: float
) -> float:
    """Additional uninterrupted work needed, including any upcoming lunch deduction."""
    if credited_seconds(work_seconds, break_seconds) >= target:
        return 0
    threshold = LUNCH_THRESHOLD_HOURS * 3600
    if work_seconds <= threshold and target <= threshold:
        return max(0, target - work_seconds)
    missing_lunch = max(0, LUNCH_DURATION_MINUTES * 60 - break_seconds)
    return max(0, target + missing_lunch - work_seconds)


def day_timeline(
    sessions: list[WorkSession], now: datetime
) -> tuple[datetime, datetime, list[Interval]]:
    start = min(session.start_time for session in sessions)
    end = max(session.end_time or now for session in sessions)
    work = merge_intervals(
        [
            interval
            for session in sessions
            for interval in session_work_intervals(session, now)
        ]
    )
    return start, end, work


def calculate_day(sessions: list[WorkSession], now: datetime) -> DailySummary | None:
    sessions = [
        session for session in sessions if session.status in ("completed", "active")
    ]
    if not sessions:
        return None
    start, end, work = day_timeline(sessions, now)
    breaks = interval_gaps(start, end, work)
    actual = interval_seconds(work)
    break_seconds = interval_seconds(breaks)
    credited = credited_seconds(actual, break_seconds)
    target = int(DAILY_REQUIREMENT_MINUTES * 60)
    remaining_daily = ceil(remaining_work_seconds(actual, break_seconds, target))
    remaining_max = ceil(
        remaining_work_seconds(actual, break_seconds, MAX_DAILY_SECONDS)
    )
    overtime = int(credited) - target
    return DailySummary(
        date=sessions[0].date.isoformat(),
        start_time=start,
        end_time=end,
        session_count=len(sessions),
        actual_work_seconds=int(actual),
        actual_work_formatted=format_duration(int(actual)),
        credited_work_seconds=int(credited),
        credited_work_formatted=format_duration(int(credited)),
        pause_count=len(breaks),
        total_pause_seconds=int(break_seconds),
        total_pause_formatted=format_duration(int(break_seconds)),
        lunch_deduction_seconds=int(actual - credited),
        lunch_deduction_formatted=format_duration(int(actual - credited)),
        lunch_break_applies=actual > LUNCH_THRESHOLD_HOURS * 3600,
        remaining_for_earliest_seconds=ceil(
            remaining_work_seconds(actual, break_seconds, MIN_WORK_HOURS * 3600)
        ),
        remaining_for_daily_seconds=remaining_daily,
        remaining_for_daily=format_duration(remaining_daily),
        remaining_for_max_seconds=remaining_max,
        remaining_for_max=format_duration(remaining_max),
        overtime_seconds=overtime,
        overtime_formatted=format_duration(overtime),
        target_reached=credited >= target,
        cap_reached=credited >= MAX_DAILY_SECONDS,
        cap_exceeded=credited > MAX_DAILY_SECONDS,
    )


def calculate_capped_end_time(
    sessions: list[WorkSession], active: WorkSession, now: datetime
) -> datetime | None:
    """Find the first cap crossing since this session started, including during pauses.

    Work can lose lunch credit at the threshold, so binary search over time
    would be incorrect. Each work/break interval has an explicit crossing rule.
    """
    start, _, work = day_timeline(sessions, now)
    cursor = active.start_time
    worked = interval_seconds(
        [(left, min(right, cursor)) for left, right in work if left < cursor]
    )
    breaks = (cursor - start).total_seconds() - worked
    if credited_seconds(worked, breaks) >= MAX_DAILY_SECONDS:
        return cursor
    boundaries = sorted(
        {cursor, now}
        | {point for interval in work for point in interval if cursor < point < now}
    )
    for left, right in zip(boundaries, boundaries[1:]):
        duration = (right - left).total_seconds()
        working = any(begin <= left < end for begin, end in work)
        if working:
            needed = remaining_work_seconds(worked, breaks, MAX_DAILY_SECONDS)
            if needed <= duration:
                return left + timedelta(seconds=needed)
            worked += duration
        else:
            # A real break can replace an automatic deduction without more work.
            if worked > LUNCH_THRESHOLD_HOURS * 3600 and worked >= MAX_DAILY_SECONDS:
                needed = max(
                    0,
                    LUNCH_DURATION_MINUTES * 60 - (worked - MAX_DAILY_SECONDS) - breaks,
                )
                if needed <= duration:
                    return left + timedelta(seconds=needed)
            breaks += duration
    return None
