import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import WorkSession, PausePeriod, TimerState
from app.schemas import StatusResponse, SessionInfo, Calculations, ActionResponse
from app.services.calculations import (
    calculate_capped_end_time,
    calculate_day,
    calculate_net_work_seconds,
    calculate_pause_seconds,
    format_duration,
    format_time,
    MIN_WORK_HOURS,
)
from app.services.workdays import load_day_sessions
from app.config import LUNCH_THRESHOLD_HOURS

logger = logging.getLogger(__name__)


@contextmanager
def _timer_transaction(db: Session) -> Iterator[None]:
    """Serialize cap decisions and history writes before reading timer state.

    SQLite's legacy driver does not begin transactions for SELECT. An explicit
    write reservation prevents a poll from combining old timer state with newly
    committed history. Helpers flush; this boundary owns commit and rollback.
    """
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        db.expire_all()
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def _prepare_history_change(
    db: Session, work_date: date, now: datetime
) -> WorkSession | None:
    active = get_active_session(db)
    if active is None or active.date != work_date:
        return None
    state = get_or_create_timer_state(db)
    if _enforce_daily_cap(db, active, state, now):
        return None
    return active


def _complete_history_change(
    db: Session, active: WorkSession | None, now: datetime
) -> None:
    db.flush()
    if active is None:
        return
    sessions = load_day_sessions(db, active.date, include_active=True)
    day = calculate_day(sessions, now)
    if day is not None and calculate_capped_end_time(sessions, active, now) is not None:
        # The correction caused this cap crossing. Preserve all work through now.
        state = get_or_create_timer_state(db)
        _finish_session(db, active, state, now)
        logger.warning(
            "History correction reached daily cap; preserving active session through correction time",
            extra={
                "session_id": active.id,
                "work_date": str(active.date),
                "end_time": now.isoformat(),
                "credited_seconds": day.credited_work_seconds,
            },
        )


def get_or_create_timer_state(db: Session) -> TimerState:
    state = db.query(TimerState).filter(TimerState.id == 1).first()
    if not state:
        state = TimerState(id=1, is_running=False, is_paused=False)
        db.add(state)
        db.flush()
        db.refresh(state)
    return state


def get_current_status(db: Session) -> str:
    state = get_or_create_timer_state(db)
    if not state.current_session_id:
        return "idle"
    return "paused" if state.is_paused else "running"


def get_active_session(db: Session) -> WorkSession | None:
    state = get_or_create_timer_state(db)
    if state.current_session_id:
        return db.get(WorkSession, state.current_session_id)
    return None


def start_blocked_reason(sessions: list[WorkSession], now: datetime) -> str | None:
    day = calculate_day(sessions, now)
    if day and day.cap_reached:
        return "Tagesmaximum erreicht. Heute ist keine weitere Erfassung möglich."
    # This provisional session counts the gap without persisting it.
    candidate = WorkSession(date=now.date(), start_time=now, status="active")
    resumed = calculate_day([*sessions, candidate], now)
    if resumed.cap_reached:
        return "Mit der Pausengutschrift wäre das Tagesmaximum bereits erreicht. Kein weiterer Start möglich."
    return None


def _finish_session(
    db: Session, session: WorkSession, state: TimerState, end: datetime
) -> None:
    end = max(end, session.start_time)
    # Late polling must not leave pauses beyond the corrected session end.
    for pause in session.pause_periods:
        pause.pause_start = min(end, max(session.start_time, pause.pause_start))
        pause.pause_end = min(end, max(pause.pause_start, pause.pause_end or end))
    session.end_time = end
    session.net_seconds = calculate_net_work_seconds(session, end)
    session.status = "completed"
    state.current_session_id = None
    state.is_running = False
    state.is_paused = False
    db.flush()


def _enforce_daily_cap(
    db: Session, session: WorkSession, state: TimerState, now: datetime
) -> bool:
    sessions = load_day_sessions(db, session.date, include_active=True)
    capped_end = calculate_capped_end_time(sessions, session, now)
    if capped_end is None:
        return False
    session_id = session.id
    work_date = session.date
    if capped_end <= session.start_time:
        logger.warning(
            "Daily cap already reached before active session; preserving recorded work",
            extra={
                "session_id": session_id,
                "work_date": str(work_date),
                "capped_end": capped_end.isoformat(),
                "now": now.isoformat(),
            },
        )
        _finish_session(db, session, state, now)
        return True
    _finish_session(db, session, state, capped_end)
    logger.info(
        "Daily cap stopped session",
        extra={
            "session_id": session_id,
            "work_date": str(work_date),
            "capped_end": capped_end.isoformat(),
        },
    )
    return True


def get_status(db: Session) -> StatusResponse:
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)
        session = get_active_session(db)
        now = datetime.now()
        auto_stopped = bool(session and _enforce_daily_cap(db, session, state, now))
        if auto_stopped:
            session = None
        work_date = session.date if session else now.date()
        sessions = load_day_sessions(db, work_date, include_active=session is not None)
        day = calculate_day(sessions, now)
        blocked_reason = None if session else start_blocked_reason(sessions, now)
        session_info = None
        calculations = None
        if session:
            actual = calculate_net_work_seconds(session, now)
            session_info = SessionInfo(
                id=session.id,
                start_time=session.start_time,
                current_time=now,
                net_work_seconds=actual,
                net_work_formatted=format_duration(actual),
                pause_count=sum(
                    p.pause_end is None or p.pause_end > p.pause_start
                    for p in session.pause_periods
                ),
                total_pause_seconds=calculate_pause_seconds(session, now),
            )
        if day:
            earliest_remaining = day.remaining_for_earliest_seconds
            calculations = Calculations(
                earliest_reached=day.credited_work_seconds >= MIN_WORK_HOURS * 3600,
                lunch_break_applies=day.lunch_break_applies,
                lunch_break_at=format_time(
                    now
                    + timedelta(
                        seconds=max(
                            0, LUNCH_THRESHOLD_HOURS * 3600 - day.actual_work_seconds
                        )
                    )
                )
                if session and not day.lunch_break_applies
                else None,
                earliest_leave=format_time(now + timedelta(seconds=earliest_remaining))
                if session
                else None,
                normal_leave=format_time(
                    now + timedelta(seconds=day.remaining_for_daily_seconds)
                )
                if session
                else None,
                latest_leave=format_time(
                    now + timedelta(seconds=day.remaining_for_max_seconds)
                )
                if session
                else None,
                remaining_for_daily=day.remaining_for_daily,
                overtime_seconds=day.overtime_seconds,
                overtime_formatted=day.overtime_formatted,
            )
        status = (
            "idle" if session is None else ("paused" if state.is_paused else "running")
        )
        return StatusResponse(
            status=status,
            session=session_info,
            day=day,
            calculations=calculations,
            auto_stopped=auto_stopped,
            can_start=session is None and blocked_reason is None,
            start_blocked_reason=blocked_reason,
        )


def start_timer(db: Session) -> ActionResponse:
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)
        if state.current_session_id:
            return ActionResponse(
                success=False,
                message="Der Timer läuft bereits.",
                status="paused" if state.is_paused else "running",
            )
        now = datetime.now()
        reason = start_blocked_reason(load_day_sessions(db, now.date()), now)
        if reason:
            logger.info(
                "Daily cap prevented start",
                extra={"work_date": now.date().isoformat(), "reason": reason},
            )
            return ActionResponse(success=False, message=reason, status="idle")
        session = WorkSession(date=now.date(), start_time=now, status="active")
        db.add(session)
        db.flush()
        db.refresh(session)
        # A losing concurrent start must discard its newly created session.
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
            db.flush()
            current_state = get_or_create_timer_state(db)
            return ActionResponse(
                success=False,
                message="Der Timer läuft bereits.",
                status="paused" if current_state.is_paused else "running",
            )
        db.flush()
        return ActionResponse(
            success=True, message="Timer gestartet.", status="running"
        )


def pause_timer(db: Session) -> ActionResponse:
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)
        session = get_active_session(db)
        if not session:
            return ActionResponse(
                success=False, message="Kein aktiver Eintrag.", status="idle"
            )
        now = datetime.now()
        if _enforce_daily_cap(db, session, state, now):
            return ActionResponse(
                success=True,
                message="Tagesmaximum erreicht. Timer automatisch gestoppt.",
                status="idle",
            )
        if state.is_paused:
            return ActionResponse(
                success=False,
                message="Der Timer ist bereits pausiert.",
                status="paused",
            )
        session.pause_periods.append(PausePeriod(pause_start=now))
        state.is_paused = True
        state.is_running = False
        db.flush()
        return ActionResponse(success=True, message="Timer pausiert.", status="paused")


def continue_timer(db: Session) -> ActionResponse:
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)
        session = get_active_session(db)
        if not session:
            return ActionResponse(
                success=False, message="Kein aktiver Eintrag.", status="idle"
            )
        now = datetime.now()
        if _enforce_daily_cap(db, session, state, now):
            return ActionResponse(
                success=True,
                message="Tagesmaximum erreicht. Timer automatisch gestoppt.",
                status="idle",
            )
        if not state.is_paused:
            return ActionResponse(
                success=False, message="Der Timer ist nicht pausiert.", status="running"
            )
        for pause in session.pause_periods:
            if pause.pause_end is None:
                pause.pause_end = now
        state.is_paused = False
        state.is_running = True
        db.flush()
        return ActionResponse(
            success=True, message="Timer fortgesetzt.", status="running"
        )


def stop_timer(db: Session) -> ActionResponse:
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)
        session = get_active_session(db)
        if not session:
            return ActionResponse(
                success=False, message="Kein aktiver Eintrag.", status="idle"
            )
        now = datetime.now()
        if _enforce_daily_cap(db, session, state, now):
            return ActionResponse(
                success=True,
                message="Tagesmaximum erreicht. Timer automatisch gestoppt.",
                status="idle",
            )
        _finish_session(db, session, state, now)
        return ActionResponse(
            success=True,
            message="Timer gestoppt und Eintrag gespeichert.",
            status="idle",
        )


def reset_timer(db: Session) -> ActionResponse:
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)
        session = get_active_session(db)
        if not session:
            return ActionResponse(
                success=False, message="Kein aktiver Eintrag.", status="idle"
            )
        session.end_time = datetime.now()
        session.status = "reset"
        state.current_session_id = None
        state.is_running = False
        state.is_paused = False
        db.flush()
        return ActionResponse(
            success=True, message="Aktueller Eintrag verworfen.", status="idle"
        )


def create_manual_session(
    db: Session, date_str: str, start_time: str, end_time: str
) -> ActionResponse:
    """Create a completed session for a past day (manual entry)"""
    with _timer_transaction(db):
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

        now = datetime.now()
        if end_dt > now:
            raise HTTPException(status_code=422, detail="Session must be in the past")

        active = _prepare_history_change(db, session_date, now)
        session = WorkSession(
            date=session_date,
            start_time=start_dt,
            end_time=end_dt,
            net_seconds=int((end_dt - start_dt).total_seconds()),
            status="completed",
        )
        db.add(session)
        _complete_history_change(db, active, now)

        return ActionResponse(
            success=True, message="Eintrag angelegt.", status=get_current_status(db)
        )


def delete_session(db: Session, session_id: int) -> ActionResponse:
    """Delete a completed work session by ID"""
    with _timer_transaction(db):
        state = get_or_create_timer_state(db)

        # Cannot delete the currently active session
        if state.current_session_id == session_id:
            raise HTTPException(
                status_code=409, detail="Cannot delete the currently active session"
            )

        session = db.query(WorkSession).filter(WorkSession.id == session_id).first()

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        now = datetime.now()
        active = _prepare_history_change(db, session.date, now)
        # Delete the session (cascade will delete related pause periods)
        db.delete(session)
        _complete_history_change(db, active, now)

        return ActionResponse(
            success=True, message="Eintrag gelöscht.", status=get_current_status(db)
        )


def update_session(
    db: Session, session_id: int, start_time: str | None, end_time: str | None
) -> ActionResponse:
    """Update start_time and/or end_time of a completed session, recalculating net_seconds."""
    with _timer_transaction(db):
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
                    session_date.year,
                    session_date.month,
                    session_date.day,
                    hour,
                    minute,
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
                    session_date.year,
                    session_date.month,
                    session_date.day,
                    hour,
                    minute,
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

        now = datetime.now()
        if new_end and new_end > now:
            raise HTTPException(status_code=422, detail="Session must be in the past")

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

        active = _prepare_history_change(db, session.date, now)
        session.start_time = new_start
        if new_end:
            session.end_time = new_end
        session.net_seconds = calculate_net_work_seconds(session, session.end_time)
        _complete_history_change(db, active, now)

        return ActionResponse(
            success=True, message="Eintrag aktualisiert.", status=get_current_status(db)
        )
