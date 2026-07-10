from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import WorkSession, PausePeriod, TimerState
from app.schemas import StatusResponse, SessionInfo, Calculations, ActionResponse
from app.services.calculations import (
    calculate_capped_end_time,
    calculate_net_work_seconds,
    calculate_pause_seconds,
    calculate_earliest_leave,
    calculate_normal_leave,
    calculate_latest_leave,
    calculate_lunch_break_time,
    calculate_remaining_for_daily,
    calculate_overtime_seconds,
    format_duration,
    format_time,
)
from app.config import LUNCH_THRESHOLD_HOURS, MAX_DAILY_SECONDS


def get_or_create_timer_state(db: Session) -> TimerState:
    """Get or create the singleton timer state record"""
    state = db.query(TimerState).filter(TimerState.id == 1).first()
    if not state:
        state = TimerState(id=1, is_running=False, is_paused=False)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def get_current_status(db: Session) -> str:
    """Return the timer state as a status string: idle, running or paused"""
    state = get_or_create_timer_state(db)
    if not state.current_session_id:
        return "idle"
    return "paused" if state.is_paused else "running"


def get_active_session(db: Session) -> WorkSession | None:
    """Get the current active session if any"""
    state = get_or_create_timer_state(db)
    if state.current_session_id:
        return (
            db.query(WorkSession)
            .filter(WorkSession.id == state.current_session_id)
            .first()
        )
    return None


def get_status(db: Session) -> StatusResponse:
    """Get the current timer status with all calculations"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)
    now = datetime.now()

    if not session:
        return StatusResponse(status="idle", session=None, calculations=None)

    net_work_seconds = calculate_net_work_seconds(session, now)

    # Auto-stop when net work reaches the daily maximum
    if net_work_seconds >= MAX_DAILY_SECONDS:
        _auto_stop_session(db, session, state, now)
        return StatusResponse(
            status="idle",
            session=None,
            calculations=None,
            auto_stopped=True,
        )

    status = "paused" if state.is_paused else "running"
    pause_seconds = calculate_pause_seconds(session, now)

    session_info = SessionInfo(
        id=session.id,
        start_time=session.start_time,
        current_time=now,
        net_work_seconds=net_work_seconds,
        net_work_formatted=format_duration(net_work_seconds),
        pause_count=len(session.pause_periods),
        total_pause_seconds=pause_seconds,
    )

    net_work_minutes = net_work_seconds / 60
    lunch_applies = net_work_minutes > LUNCH_THRESHOLD_HOURS * 60
    overtime_seconds = calculate_overtime_seconds(net_work_seconds, lunch_applies)

    calculations = Calculations(
        lunch_break_applies=lunch_applies,
        lunch_break_at=format_time(
            calculate_lunch_break_time(session.start_time, pause_seconds)
        )
        if not lunch_applies
        else None,
        earliest_leave=format_time(
            calculate_earliest_leave(session.start_time, pause_seconds)
        ),
        normal_leave=format_time(
            calculate_normal_leave(session.start_time, pause_seconds)
        ),
        latest_leave=format_time(
            calculate_latest_leave(session.start_time, pause_seconds)
        ),
        remaining_for_daily=format_duration(
            calculate_remaining_for_daily(net_work_seconds, lunch_applies)
        ),
        overtime_seconds=overtime_seconds,
        overtime_formatted=format_duration(overtime_seconds),
    )

    return StatusResponse(
        status=status, session=session_info, calculations=calculations
    )


def start_timer(db: Session) -> ActionResponse:
    """Start a new work session"""
    state = get_or_create_timer_state(db)

    if state.current_session_id:
        return ActionResponse(
            success=False,
            message="Timer already running",
            status="paused" if state.is_paused else "running",
        )

    now = datetime.now()
    session = WorkSession(
        date=now.date(),
        start_time=now,
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # Compare-and-set update prevents a race where concurrent starts could both
    # create sessions and overwrite TimerState.current_session_id.
    rows_updated = (
        db.query(TimerState)
        .filter(TimerState.id == 1, TimerState.current_session_id.is_(None))
        .update(
            {
                TimerState.current_session_id: session.id,
                TimerState.is_running: True,
                TimerState.is_paused: False,
            },
            synchronize_session=False,
        )
    )

    if rows_updated == 0:
        db.delete(session)
        db.commit()
        current_state = get_or_create_timer_state(db)
        return ActionResponse(
            success=False,
            message="Timer already running",
            status="paused" if current_state.is_paused else "running",
        )

    db.commit()

    return ActionResponse(success=True, message="Timer started", status="running")


def pause_timer(db: Session) -> ActionResponse:
    """Pause the current session"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)

    if not session:
        return ActionResponse(success=False, message="No active session", status="idle")

    if state.is_paused:
        return ActionResponse(
            success=False, message="Timer already paused", status="paused"
        )

    now = datetime.now()
    pause = PausePeriod(session_id=session.id, pause_start=now)
    db.add(pause)

    state.is_paused = True
    state.is_running = False
    db.commit()

    return ActionResponse(success=True, message="Timer paused", status="paused")


def continue_timer(db: Session) -> ActionResponse:
    """Resume from pause"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)

    if not session:
        return ActionResponse(success=False, message="No active session", status="idle")

    if not state.is_paused:
        return ActionResponse(
            success=False, message="Timer not paused", status="running"
        )

    # Find the active pause and end it
    active_pause = (
        db.query(PausePeriod)
        .filter(PausePeriod.session_id == session.id, PausePeriod.pause_end.is_(None))
        .first()
    )

    if active_pause:
        active_pause.pause_end = datetime.now()

    state.is_paused = False
    state.is_running = True
    db.commit()

    return ActionResponse(success=True, message="Timer resumed", status="running")


def stop_timer(db: Session) -> ActionResponse:
    """Stop and save the current session"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)

    if not session:
        return ActionResponse(success=False, message="No active session", status="idle")

    now = datetime.now()

    # End any active pause
    active_pause = (
        db.query(PausePeriod)
        .filter(PausePeriod.session_id == session.id, PausePeriod.pause_end.is_(None))
        .first()
    )
    if active_pause:
        active_pause.pause_end = now

    # Calculate and save net work time
    session.end_time = now
    session.net_seconds = calculate_net_work_seconds(session, now)
    session.status = "completed"

    # Reset timer state
    state.current_session_id = None
    state.is_running = False
    state.is_paused = False
    db.commit()

    return ActionResponse(
        success=True, message="Timer stopped and saved", status="idle"
    )


def reset_timer(db: Session) -> ActionResponse:
    """Stop without saving (discard session)"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)

    if not session:
        return ActionResponse(success=False, message="No active session", status="idle")

    # Mark session as reset instead of deleting for audit trail
    session.end_time = datetime.now()
    session.status = "reset"

    # Reset timer state
    state.current_session_id = None
    state.is_running = False
    state.is_paused = False
    db.commit()

    return ActionResponse(
        success=True, message="Timer reset (session discarded)", status="idle"
    )


def create_manual_session(
    db: Session, date_str: str, start_time: str, end_time: str
) -> ActionResponse:
    """Create a completed session for a past day (manual entry)"""
    try:
        session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_hour, start_minute = map(int, start_time.split(":"))
        end_hour, end_minute = map(int, end_time.split(":"))
        start_dt = datetime(
            session_date.year,
            session_date.month,
            session_date.day,
            start_hour,
            start_minute,
        )
        end_dt = datetime(
            session_date.year,
            session_date.month,
            session_date.day,
            end_hour,
            end_minute,
        )
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422,
            detail="Invalid date or time format (expected YYYY-MM-DD and HH:MM)",
        )

    if start_dt >= end_dt:
        raise HTTPException(
            status_code=422, detail="Start time must be before end time"
        )

    if end_dt > datetime.now():
        raise HTTPException(status_code=422, detail="Session must be in the past")

    session = WorkSession(
        date=session_date,
        start_time=start_dt,
        end_time=end_dt,
        net_seconds=min(int((end_dt - start_dt).total_seconds()), MAX_DAILY_SECONDS),
        status="completed",
    )
    db.add(session)
    db.commit()

    return ActionResponse(
        success=True, message="Session created", status=get_current_status(db)
    )


def delete_session(db: Session, session_id: int) -> ActionResponse:
    """Delete a completed work session by ID"""
    state = get_or_create_timer_state(db)

    # Cannot delete the currently active session
    if state.current_session_id == session_id:
        raise HTTPException(
            status_code=409, detail="Cannot delete the currently active session"
        )

    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete the session (cascade will delete related pause periods)
    db.delete(session)
    db.commit()

    return ActionResponse(
        success=True, message="Session deleted", status=get_current_status(db)
    )


def update_session(
    db: Session, session_id: int, start_time: str | None, end_time: str | None
) -> ActionResponse:
    """Update start_time and/or end_time of a completed session, recalculating net_seconds."""
    state = get_or_create_timer_state(db)

    # Block editing the active session
    if state.current_session_id == session_id:
        raise HTTPException(
            status_code=409, detail="Cannot edit the currently active session"
        )

    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only completed sessions have a reliable end_time to recalculate against
    if session.status != "completed":
        raise HTTPException(
            status_code=409, detail="Only completed sessions can be edited"
        )

    if not start_time and not end_time:
        raise HTTPException(status_code=422, detail="No changes provided")

    session_date = session.start_time.date()

    # Parse and apply new start_time
    new_start = session.start_time
    if start_time:
        try:
            hour, minute = map(int, start_time.split(":"))
            new_start = datetime(
                session_date.year, session_date.month, session_date.day, hour, minute
            )
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=422, detail="Invalid start_time format (expected HH:MM)"
            )

    # Parse and apply new end_time
    new_end = session.end_time
    if end_time:
        try:
            hour, minute = map(int, end_time.split(":"))
            new_end = datetime(
                session_date.year, session_date.month, session_date.day, hour, minute
            )
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=422, detail="Invalid end_time format (expected HH:MM)"
            )

    # Validate: start < end
    if new_end and new_start >= new_end:
        raise HTTPException(
            status_code=422, detail="Start time must be before end time"
        )

    # Validate against pause periods
    pauses = sorted(session.pause_periods, key=lambda p: p.pause_start)
    if pauses:
        first_pause_start = pauses[0].pause_start
        last_pause_end = pauses[-1].pause_end
        if new_start >= first_pause_start:
            raise HTTPException(
                status_code=422, detail="Start time must be before the first pause"
            )
        if last_pause_end and new_end and new_end < last_pause_end:
            raise HTTPException(
                status_code=422, detail="End time must be after the last pause"
            )

    session.start_time = new_start
    if new_end:
        session.end_time = new_end
    session.net_seconds = calculate_net_work_seconds(session, session.end_time)
    db.commit()

    return ActionResponse(
        success=True, message="Session updated", status=get_current_status(db)
    )


def _auto_stop_session(
    db: Session, session: WorkSession, state: TimerState, now: datetime
) -> None:
    """Auto-stop a session that has reached the daily maximum work time."""
    # The cap may have been reached long before this poll, so end the session at
    # the exact moment net work hit the limit instead of at `now`.
    capped_end = calculate_capped_end_time(session, now)

    # An open pause can only start after the cap was reached (net work does not
    # grow while paused), so clamp it to zero length rather than counting it.
    active_pause = (
        db.query(PausePeriod)
        .filter(PausePeriod.session_id == session.id, PausePeriod.pause_end.is_(None))
        .first()
    )
    if active_pause:
        active_pause.pause_end = max(active_pause.pause_start, min(now, capped_end))

    session.end_time = capped_end
    session.net_seconds = MAX_DAILY_SECONDS
    session.status = "completed"

    state.current_session_id = None
    state.is_running = False
    state.is_paused = False
    db.commit()
