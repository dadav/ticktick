from pathlib import Path
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import statistics
from app.version import get_version

router = APIRouter(tags=["pages"])

templates_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=templates_path)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main timer page"""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "active_page": "timer", "version": get_version()},
    )


@router.get("/statistics", response_class=HTMLResponse)
async def statistics_page(request: Request, db: Session = Depends(get_db)):
    """Render the statistics page"""
    stats = statistics.get_statistics(db)
    return templates.TemplateResponse(
        "statistics.html",
        {
            "request": request,
            "active_page": "statistics",
            "stats": stats,
            "version": get_version(),
        },
    )
