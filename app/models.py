from datetime import datetime, date
from sqlalchemy import ForeignKey, Date, DateTime, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WorkSession(Base):
    __tablename__ = "work_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    net_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    pause_periods: Mapped[list["PausePeriod"]] = relationship(
        "PausePeriod", back_populates="session", cascade="all, delete-orphan"
    )


class PausePeriod(Base):
    __tablename__ = "pause_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("work_sessions.id"), nullable=False)
    pause_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pause_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="pause_periods")


class TimerState(Base):
    __tablename__ = "timer_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_sessions.id"), nullable=True)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
