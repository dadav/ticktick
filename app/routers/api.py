from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import StatusResponse, ActionResponse, StatisticsResponse
from app.services import timer
from app.services import statistics

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
