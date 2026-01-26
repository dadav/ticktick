from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import WorkSession
from app.schemas import StatisticsResponse, WeekSummary, MonthSummary, SessionSummary
from app.services.calculations import format_duration, calculate_overtime_seconds
from app.config import WEEKLY_HOURS


def get_week_start(date: datetime) -> datetime:
    """Get the Monday of the week containing the given date"""
    return date - timedelta(days=date.weekday())


def get_month_start(date: datetime) -> datetime:
    """Get the first day of the month containing the given date"""
    return date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


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

    week_summary = WeekSummary(
        total_seconds=week_total_seconds,
        total_formatted=format_duration(week_total_seconds),
        target_seconds=week_target_seconds,
        target_formatted=format_duration(week_target_seconds),
        days_worked=week_days_worked,
        avg_per_day_formatted=format_duration(week_avg_seconds),
        overtime_seconds=week_overtime_seconds,
        overtime_formatted=format_duration(week_overtime_seconds),
    )

    # Calculate month summary
    month_total_seconds = sum(s.net_seconds or 0 for s in month_sessions)
    month_days_worked = len(set(s.date for s in month_sessions))
    month_avg_seconds = month_total_seconds // month_days_worked if month_days_worked > 0 else 0

    month_summary = MonthSummary(
        total_seconds=month_total_seconds,
        total_formatted=format_duration(month_total_seconds),
        days_worked=month_days_worked,
        avg_per_day_formatted=format_duration(month_avg_seconds),
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
                net_work_formatted=format_duration(net_seconds),
                overtime_seconds=overtime,
                overtime_formatted=format_duration(overtime),
                status=s.status,
            )
        )

    return StatisticsResponse(
        this_week=week_summary,
        this_month=month_summary,
        recent_sessions=recent_sessions,
    )
