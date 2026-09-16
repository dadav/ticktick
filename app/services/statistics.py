from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session, selectinload

from app.config import DAILY_REQUIREMENT_MINUTES
from app.models import WorkSession
from app.schemas import (
    StatisticsResponse,
    WeekSummary,
    MonthSummary,
    SessionSummary,
    SessionDetailResponse,
    PausePeriodInfo,
    DayHistory,
)
from app.services.calculations import (
    calculate_day,
    calculate_pause_seconds,
    calculate_net_work_seconds,
    format_duration,
    format_duration_short,
)
from app.services.workdays import load_day_sessions


def get_week_start(value: datetime) -> datetime:
    return value - timedelta(days=value.weekday())


def get_month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def group_sessions(sessions: list[WorkSession]) -> dict[date, list[WorkSession]]:
    groups: dict[date, list[WorkSession]] = {}
    for session in sessions:
        groups.setdefault(session.date, []).append(session)
    return groups


def calculate_average_times(
    sessions: list[WorkSession],
) -> tuple[str | None, str | None]:
    starts = []
    ends = []
    for group in group_sessions(sessions).values():
        start = min(session.start_time for session in group)
        starts.append(start.hour * 3600 + start.minute * 60 + start.second)
        completed_ends = [session.end_time for session in group if session.end_time]
        if completed_ends:
            end = max(completed_ends)
            ends.append(end.hour * 3600 + end.minute * 60 + end.second)
    return (
        format_duration_short(sum(starts) // len(starts)) if starts else None,
        format_duration_short(sum(ends) // len(ends)) if ends else None,
    )


def summarize_sessions(sessions: list[WorkSession]) -> tuple[int, int, int]:
    groups = group_sessions(sessions)
    now = datetime.now()
    days = [calculate_day(group, now) for group in groups.values()]
    days = [day for day in days if day is not None]
    return (
        sum(day.credited_work_seconds for day in days),
        len(days),
        len(days) * int(DAILY_REQUIREMENT_MINUTES * 60),
    )


def session_summary(
    session: WorkSession, overtime: int, now: datetime
) -> SessionSummary:
    return SessionSummary(
        id=session.id,
        date=session.date.isoformat(),
        start_time=session.start_time.strftime("%H:%M"),
        end_time=session.end_time.strftime("%H:%M") if session.end_time else None,
        net_work_formatted=format_duration_short(
            calculate_net_work_seconds(session, now)
        ),
        overtime_seconds=overtime,
        overtime_formatted=format_duration_short(overtime),
        status=session.status,
    )


def get_statistics(db: Session) -> StatisticsResponse:
    now = datetime.now()
    today = now.date()
    week_start = get_week_start(now).date()
    month_start = get_month_start(now).date()
    completed = (
        db.query(WorkSession)
        .options(selectinload(WorkSession.pause_periods))
        .filter(WorkSession.status == "completed")
    )
    period_sessions = completed.filter(
        WorkSession.date >= min(week_start, month_start), WorkSession.date <= today
    ).all()
    week_sessions = [s for s in period_sessions if s.date >= week_start]
    month_sessions = [s for s in period_sessions if s.date >= month_start]
    week_total, week_days, week_target = summarize_sessions(week_sessions)
    month_total, month_days, month_target = summarize_sessions(month_sessions)
    week_start_avg, week_end_avg = calculate_average_times(week_sessions)
    month_start_avg, month_end_avg = calculate_average_times(month_sessions)
    week_summary = WeekSummary(
        total_seconds=week_total,
        total_formatted=format_duration(week_total),
        target_seconds=week_target,
        target_formatted=format_duration(week_target),
        days_worked=week_days,
        avg_per_day_formatted=format_duration(
            week_total // week_days if week_days else 0
        ),
        overtime_seconds=week_total - week_target,
        overtime_formatted=format_duration(week_total - week_target),
        average_start_time=week_start_avg,
        average_end_time=week_end_avg,
    )
    month_summary = MonthSummary(
        total_seconds=month_total,
        total_formatted=format_duration(month_total),
        days_worked=month_days,
        avg_per_day_formatted=format_duration(
            month_total // month_days if month_days else 0
        ),
        overtime_seconds=month_total - month_target,
        overtime_formatted=format_duration(month_total - month_target),
        average_start_time=month_start_avg,
        average_end_time=month_end_avg,
    )
    recent_dates = [
        row[0]
        for row in db.query(WorkSession.date)
        .filter(
            WorkSession.status == "completed",
            WorkSession.date <= today,
        )
        .distinct()
        .order_by(WorkSession.date.desc())
        .limit(10)
        .all()
    ]
    # Keep the flat response for existing clients, while always loading full days.
    recent = (
        completed.order_by(
            WorkSession.date.desc(),
            WorkSession.start_time.desc(),
            WorkSession.id.desc(),
        )
        .limit(10)
        .all()
    )
    dates = set(recent_dates) | {s.date for s in recent}
    history_sessions = (
        completed.filter(WorkSession.date.in_(dates))
        .order_by(WorkSession.start_time, WorkSession.id)
        .all()
        if dates
        else []
    )
    groups = group_sessions(history_sessions)
    days = {day: calculate_day(group, now) for day, group in groups.items()}
    recent_days = [
        DayHistory(
            day=days[day],
            sessions=[
                session_summary(s, days[day].overtime_seconds, now) for s in groups[day]
            ],
        )
        for day in recent_dates
    ]
    return StatisticsResponse(
        this_week=week_summary,
        this_month=month_summary,
        recent_sessions=[
            session_summary(s, days[s.date].overtime_seconds, now) for s in recent
        ],
        recent_days=recent_days,
    )


def get_session_details(db: Session, session_id: int) -> SessionDetailResponse | None:
    session = db.get(WorkSession, session_id)
    if not session:
        return None
    now = datetime.now()
    actual = calculate_net_work_seconds(session, now)
    end = session.end_time or now
    day = calculate_day(load_day_sessions(db, session.date, include_active=True), now)
    overtime = day.overtime_seconds if day else 0
    pauses = []
    for pause in sorted(session.pause_periods, key=lambda p: p.pause_start):
        pause_start = max(session.start_time, pause.pause_start)
        pause_end = min(end, pause.pause_end or end)
        if pause_end <= pause_start:
            continue
        pauses.append(
            PausePeriodInfo(
                id=pause.id,
                pause_start=pause_start.strftime("%H:%M"),
                pause_end=pause_end.strftime("%H:%M") if pause.pause_end else None,
                duration_formatted=format_duration_short(
                    int((pause_end - pause_start).total_seconds())
                ),
            )
        )
    return SessionDetailResponse(
        id=session.id,
        date=session.date.isoformat(),
        start_time=session.start_time.strftime("%H:%M"),
        end_time=session.end_time.strftime("%H:%M") if session.end_time else None,
        net_work_formatted=format_duration_short(actual),
        gross_work_formatted=format_duration_short(
            int((end - session.start_time).total_seconds())
        ),
        total_pause_formatted=format_duration_short(
            calculate_pause_seconds(session, now)
        ),
        overtime_seconds=overtime,
        overtime_formatted=format_duration_short(overtime),
        status=session.status,
        pause_count=len(pauses),
        pauses=pauses,
    )
