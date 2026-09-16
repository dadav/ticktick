from datetime import date

from sqlalchemy.orm import Session, selectinload

from app.models import WorkSession


def load_day_sessions(
    db: Session, day: date, include_active: bool = False
) -> list[WorkSession]:
    statuses = ("completed", "active") if include_active else ("completed",)
    return (
        db.query(WorkSession)
        .options(selectinload(WorkSession.pause_periods))
        .filter(WorkSession.date == day, WorkSession.status.in_(statuses))
        .order_by(WorkSession.start_time, WorkSession.id)
        .all()
    )
