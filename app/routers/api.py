from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import StatusResponse, ActionResponse, StatisticsResponse, SessionDetailResponse
from app.services import timer
from app.services import statistics
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["api"])


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


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_details(session_id: int, db: Session = Depends(get_db)):
    """Get detailed information about a specific session including pauses"""
    result = statistics.get_session_details(db, session_id)
    if result is None:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})
    return result


@router.delete("/sessions/{session_id}", response_model=ActionResponse)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    """Delete a work session by ID"""
    return timer.delete_session(db, session_id)
