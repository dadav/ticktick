from datetime import datetime
from sqlalchemy.orm import Session

from app.models import WorkSession, PausePeriod, TimerState
from app.schemas import StatusResponse, SessionInfo, Calculations, ActionResponse
from app.services.calculations import (
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
from app.config import LUNCH_THRESHOLD_HOURS


def get_or_create_timer_state(db: Session) -> TimerState:
    """Get or create the singleton timer state record"""
    state = db.query(TimerState).filter(TimerState.id == 1).first()
    if not state:
        state = TimerState(id=1, is_running=False, is_paused=False)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def get_active_session(db: Session) -> WorkSession | None:
    """Get the current active session if any"""
    state = get_or_create_timer_state(db)
    if state.current_session_id:
        return db.query(WorkSession).filter(WorkSession.id == state.current_session_id).first()
    return None


def get_status(db: Session) -> StatusResponse:
    """Get the current timer status with all calculations"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)
    now = datetime.now()

    if not session:
        return StatusResponse(status="idle", session=None, calculations=None)

    status = "paused" if state.is_paused else "running"
    net_work_seconds = calculate_net_work_seconds(session, now)
    pause_seconds = calculate_pause_seconds(session, now)
    pause_minutes = pause_seconds // 60

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
    overtime_seconds = calculate_overtime_seconds(net_work_seconds)

    calculations = Calculations(
        lunch_break_applies=lunch_applies,
        lunch_break_at=format_time(calculate_lunch_break_time(session.start_time, pause_minutes)) if not lunch_applies else None,
        earliest_leave=format_time(calculate_earliest_leave(session.start_time, pause_minutes)),
        normal_leave=format_time(calculate_normal_leave(session.start_time, pause_minutes)),
        latest_leave=format_time(calculate_latest_leave(session.start_time, pause_minutes)),
        remaining_for_daily=format_duration(calculate_remaining_for_daily(net_work_seconds)),
        overtime_seconds=overtime_seconds,
        overtime_formatted=format_duration(overtime_seconds),
    )

    return StatusResponse(status=status, session=session_info, calculations=calculations)


def start_timer(db: Session) -> ActionResponse:
    """Start a new work session"""
    state = get_or_create_timer_state(db)

    if state.current_session_id:
        return ActionResponse(success=False, message="Timer already running", status="running")

    now = datetime.now()
    session = WorkSession(
        date=now.date(),
        start_time=now,
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    state.current_session_id = session.id
    state.is_running = True
    state.is_paused = False
    db.commit()

    return ActionResponse(success=True, message="Timer started", status="running")


def pause_timer(db: Session) -> ActionResponse:
    """Pause the current session"""
    state = get_or_create_timer_state(db)
    session = get_active_session(db)

    if not session:
        return ActionResponse(success=False, message="No active session", status="idle")

    if state.is_paused:
        return ActionResponse(success=False, message="Timer already paused", status="paused")

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
        return ActionResponse(success=False, message="Timer not paused", status="running")

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

    return ActionResponse(success=True, message="Timer stopped and saved", status="idle")


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

    return ActionResponse(success=True, message="Timer reset (session discarded)", status="idle")


def delete_session(db: Session, session_id: int) -> ActionResponse:
    """Delete a completed work session by ID"""
    state = get_or_create_timer_state(db)

    # Cannot delete the currently active session
    if state.current_session_id == session_id:
        return ActionResponse(
            success=False,
            message="Cannot delete the currently active session",
            status="running" if state.is_running else "paused"
        )

    session = db.query(WorkSession).filter(WorkSession.id == session_id).first()

    if not session:
        return ActionResponse(success=False, message="Session not found", status="idle")

    # Delete the session (cascade will delete related pause periods)
    db.delete(session)
    db.commit()

    return ActionResponse(success=True, message="Session deleted", status="idle")
