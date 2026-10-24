from pathlib import Path
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import (
    DAILY_REQUIREMENT_MINUTES,
    LUNCH_DURATION_MINUTES,
    MAX_DAILY_HOURS,
)
from app.database import get_db
from app.services import statistics
from app.services.calculations import MIN_WORK_HOURS, format_hours_label
from app.version import get_version

router = APIRouter(tags=["pages"])

templates_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=templates_path)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main timer page"""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_page": "timer",
            "version": get_version(),
            "min_work_label": format_hours_label(MIN_WORK_HOURS * 60),
            "daily_label": format_hours_label(DAILY_REQUIREMENT_MINUTES),
            "max_daily_label": format_hours_label(MAX_DAILY_HOURS * 60),
            "lunch_duration_minutes": LUNCH_DURATION_MINUTES,
        },
    )


@router.get("/statistics", response_class=HTMLResponse)
async def statistics_page(request: Request, db: Session = Depends(get_db)):
    """Render the statistics page"""
    stats = statistics.get_statistics(db)
    return templates.TemplateResponse(
        request,
        "statistics.html",
        {
            "active_page": "statistics",
            "stats": stats,
            "version": get_version(),
        },
    )
