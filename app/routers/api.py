from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    StatusResponse,
    ActionResponse,
    HealthResponse,
    StatisticsResponse,
    SessionDetailResponse,
    SessionCreateRequest,
    SessionUpdateRequest,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    NotificationTestResponse,
)
from app.config import GOTIFY_CONFIGURED
from app.services import timer
from app.services import statistics
from app.services import notifications
from app.services.gotify import GotifyError

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    """Liveness probe: confirms SQLite is readable without the timer write lock"""
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok")


@router.get("/status", response_model=StatusResponse)
def get_status(db: Session = Depends(get_db)):
    """Get current timer status and calculations"""
    return timer.get_status(db)


@router.post("/start", response_model=ActionResponse)
def start_timer(db: Session = Depends(get_db)):
    """Start a new work session"""
    return timer.start_timer(db)


@router.post("/pause", response_model=ActionResponse)
def pause_timer(db: Session = Depends(get_db)):
    """Pause the current session"""
    return timer.pause_timer(db)


@router.post("/continue", response_model=ActionResponse)
def continue_timer(db: Session = Depends(get_db)):
    """Resume from pause"""
    return timer.continue_timer(db)


@router.post("/stop", response_model=ActionResponse)
def stop_timer(db: Session = Depends(get_db)):
    """Stop and save the session"""
    return timer.stop_timer(db)


@router.post("/reset", response_model=ActionResponse)
def reset_timer(db: Session = Depends(get_db)):
    """Stop without saving (discard session)"""
    return timer.reset_timer(db)


@router.get("/statistics/summary", response_model=StatisticsResponse)
def get_statistics(db: Session = Depends(get_db)):
    """Get weekly/monthly statistics"""
    return statistics.get_statistics(db)


@router.post("/sessions", response_model=ActionResponse, status_code=201)
def create_session(body: SessionCreateRequest, db: Session = Depends(get_db)):
    """Manually add a completed session for a past day"""
    return timer.create_manual_session(db, body.date, body.start_time, body.end_time)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_details(session_id: int, db: Session = Depends(get_db)):
    """Get detailed information about a specific session including pauses"""
    result = statistics.get_session_details(db, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@router.put("/sessions/{session_id}", response_model=ActionResponse)
def update_session(
    session_id: int, body: SessionUpdateRequest, db: Session = Depends(get_db)
):
    """Update start/end time of a completed session"""
    return timer.update_session(db, session_id, body.start_time, body.end_time)


@router.delete("/sessions/{session_id}", response_model=ActionResponse)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    """Delete a work session by ID"""
    return timer.delete_session(db, session_id)


@router.get("/notifications", response_model=NotificationSettingsResponse)
def get_notification_settings(db: Session = Depends(get_db)):
    """Get start reminder settings and today's workday status"""
    return notifications.get_settings(db, datetime.now())


@router.put("/notifications", response_model=NotificationSettingsResponse)
def update_notification_settings(
    body: NotificationSettingsUpdate, db: Session = Depends(get_db)
):
    """Enable/disable start reminders or pause them through a date"""
    return notifications.update_settings(
        db, body.enabled, body.paused_until, datetime.now()
    )


@router.post("/notifications/test", response_model=NotificationTestResponse)
def send_test_notification():
    """Send a test message to Gotify"""
    if not GOTIFY_CONFIGURED:
        raise HTTPException(status_code=409, detail="Gotify is not configured")
    try:
        notifications.send_test_message()
    except GotifyError as error:
        raise HTTPException(status_code=502, detail="Gotify request failed") from error
    return NotificationTestResponse(
        success=True, message="Testbenachrichtigung gesendet."
    )
