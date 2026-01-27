from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import WorkSession
from app.schemas import (
    StatisticsResponse,
    WeekSummary,
    MonthSummary,
    SessionSummary,
    SessionDetailResponse,
    PausePeriodInfo,
)
from app.services.calculations import (
    format_duration,
    format_duration_short,
    calculate_overtime_seconds,
    calculate_pause_seconds,
)
from app.config import WEEKLY_HOURS


def get_week_start(date: datetime) -> datetime:
    """Get the Monday of the week containing the given date"""
    return date - timedelta(days=date.weekday())


def get_month_start(date: datetime) -> datetime:
    """Get the first day of the month containing the given date"""
    return date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def calculate_average_times(sessions: list[WorkSession]) -> tuple[str | None, str | None]:
    """
    Calculate average start and end times from a list of sessions.

    Returns tuple of (average_start_time, average_end_time) in HH:MM format.
    Returns None for each if no valid data is available.
    """
    if not sessions:
        return None, None

    # Calculate average start time
    # Convert each start time to seconds since midnight
    start_times_seconds = []
    for session in sessions:
        if session.start_time:
            start = session.start_time
            seconds_since_midnight = start.hour * 3600 + start.minute * 60 + start.second
            start_times_seconds.append(seconds_since_midnight)

    average_start = None
    if start_times_seconds:
        avg_start_seconds = sum(start_times_seconds) // len(start_times_seconds)
        hours, remainder = divmod(avg_start_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        average_start = f"{hours:02d}:{minutes:02d}"

    # Calculate average end time
    # Only include sessions with an end_time
    end_times_seconds = []
    for session in sessions:
        if session.end_time:
            end = session.end_time
            seconds_since_midnight = end.hour * 3600 + end.minute * 60 + end.second
            end_times_seconds.append(seconds_since_midnight)

    average_end = None
    if end_times_seconds:
        avg_end_seconds = sum(end_times_seconds) // len(end_times_seconds)
        hours, remainder = divmod(avg_end_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        average_end = f"{hours:02d}:{minutes:02d}"

    return average_start, average_end


def calculate_monthly_target_seconds(days_worked: int) -> int:
    """
    Calculate monthly target based on days worked.
    Uses daily requirement derived from weekly hours.
    """
    daily_requirement_seconds = int((WEEKLY_HOURS * 3600) / 5)  # 5 workdays per week
    return days_worked * daily_requirement_seconds


def get_statistics(db: Session) -> StatisticsResponse:
    """Get comprehensive statistics"""
    now = datetime.now()
    week_start = get_week_start(now).date()
    month_start = get_month_start(now).date()

    # Get completed sessions for this week
    week_sessions = (
        db.query(WorkSession)
        .filter(
            WorkSession.status == "completed",
            WorkSession.date >= week_start,
        )
        .all()
    )

    # Get completed sessions for this month
    month_sessions = (
        db.query(WorkSession)
        .filter(
            WorkSession.status == "completed",
            WorkSession.date >= month_start,
        )
        .all()
    )

    # Calculate week summary
    week_total_seconds = sum(s.net_seconds or 0 for s in week_sessions)
    week_target_seconds = int(WEEKLY_HOURS * 3600)
    week_days_worked = len(set(s.date for s in week_sessions))
    week_avg_seconds = week_total_seconds // week_days_worked if week_days_worked > 0 else 0
    week_overtime_seconds = week_total_seconds - week_target_seconds
    week_average_start, week_average_end = calculate_average_times(week_sessions)

    week_summary = WeekSummary(
        total_seconds=week_total_seconds,
        total_formatted=format_duration(week_total_seconds),
        target_seconds=week_target_seconds,
        target_formatted=format_duration(week_target_seconds),
        days_worked=week_days_worked,
        avg_per_day_formatted=format_duration(week_avg_seconds),
        overtime_seconds=week_overtime_seconds,
        overtime_formatted=format_duration(week_overtime_seconds),
        average_start_time=week_average_start,
        average_end_time=week_average_end,
    )

    # Calculate month summary
    month_total_seconds = sum(s.net_seconds or 0 for s in month_sessions)
    month_days_worked = len(set(s.date for s in month_sessions))
    month_avg_seconds = month_total_seconds // month_days_worked if month_days_worked > 0 else 0
    month_target_seconds = calculate_monthly_target_seconds(month_days_worked)
    month_overtime_seconds = month_total_seconds - month_target_seconds
    month_average_start, month_average_end = calculate_average_times(month_sessions)

    month_summary = MonthSummary(
        total_seconds=month_total_seconds,
        total_formatted=format_duration(month_total_seconds),
        days_worked=month_days_worked,
        avg_per_day_formatted=format_duration(month_avg_seconds),
        overtime_seconds=month_overtime_seconds,
        overtime_formatted=format_duration(month_overtime_seconds),
        average_start_time=month_average_start,
        average_end_time=month_average_end,
    )

    # Get recent sessions (last 10)
    recent = (
        db.query(WorkSession)
        .filter(WorkSession.status == "completed")
        .order_by(WorkSession.date.desc(), WorkSession.start_time.desc())
        .limit(10)
        .all()
    )

    recent_sessions = []
    for s in recent:
        net_seconds = s.net_seconds or 0
        overtime = calculate_overtime_seconds(net_seconds)
        recent_sessions.append(
            SessionSummary(
                id=s.id,
                date=s.date.strftime("%Y-%m-%d"),
                start_time=s.start_time.strftime("%H:%M"),
                end_time=s.end_time.strftime("%H:%M") if s.end_time else None,
                net_work_formatted=format_duration_short(net_seconds),
                overtime_seconds=overtime,
                overtime_formatted=format_duration_short(overtime),
                status=s.status,
            )
        )

    return StatisticsResponse(
        this_week=week_summary,
        this_month=month_summary,
        recent_sessions=recent_sessions,
    )


def get_session_details(db: Session, session_id: int) -> SessionDetailResponse | None:
    """Get detailed information about a specific session including all pauses"""
    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()

    if not session:
        return None

    now = datetime.now()
    net_seconds = session.net_seconds if session.net_seconds is not None else 0

    # Calculate gross work time (elapsed time from start to end/now)
    end_time = session.end_time or now
    gross_seconds = int((end_time - session.start_time).total_seconds())

    # Calculate total pause time
    pause_seconds = calculate_pause_seconds(session, now)

    overtime = calculate_overtime_seconds(net_seconds)

    # Build pause period info list
    pauses = []
    for pause in session.pause_periods:
        if pause.pause_end:
            pause_duration = int((pause.pause_end - pause.pause_start).total_seconds())
        else:
            pause_duration = int((now - pause.pause_start).total_seconds())

        pauses.append(
            PausePeriodInfo(
                id=pause.id,
                pause_start=pause.pause_start.strftime("%H:%M"),
                pause_end=pause.pause_end.strftime("%H:%M") if pause.pause_end else None,
                duration_formatted=format_duration_short(pause_duration),
            )
        )

    return SessionDetailResponse(
        id=session.id,
        date=session.date.strftime("%Y-%m-%d"),
        start_time=session.start_time.strftime("%H:%M"),
        end_time=session.end_time.strftime("%H:%M") if session.end_time else None,
        net_work_formatted=format_duration_short(net_seconds),
        gross_work_formatted=format_duration_short(gross_seconds),
        total_pause_formatted=format_duration_short(pause_seconds),
        overtime_seconds=overtime,
        overtime_formatted=format_duration_short(overtime),
        status=session.status,
        pause_count=len(pauses),
        pauses=pauses,
    )
