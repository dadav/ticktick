from datetime import datetime, date
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WorkSession(Base):
    __tablename__ = "work_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Legacy write-only cache of actual work; derive all totals from timestamps.
    net_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    pause_periods: Mapped[list["PausePeriod"]] = relationship(
        "PausePeriod", back_populates="session", cascade="all, delete-orphan"
    )


class PausePeriod(Base):
    __tablename__ = "pause_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_sessions.id"), nullable=False
    )
    pause_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pause_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    session: Mapped["WorkSession"] = relationship(
        "WorkSession", back_populates="pause_periods"
    )


class TimerState(Base):
    __tablename__ = "timer_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("work_sessions.id"), nullable=True
    )
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationSettings(Base):
    """Singleton (id=1) holding UI-controlled reminder state."""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Reminders are skipped through this date (inclusive); expired dates are cleared.
    paused_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Claimed before sending, so each day gets at most one start reminder.
    last_start_reminder_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class MilestoneSettings(Base):
    """Singleton (id=1) holding which work milestones send a notification."""

    __tablename__ = "milestone_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    notify_min_work: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    notify_daily_target: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    notify_daily_max: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class MilestoneEvent(Base):
    """One row per handled milestone of a workday; a row is never sent again.

    outcome: claimed (send in progress or done), disabled, paused, stale, baseline.
    Only a claimed row is deleted, and only after a definite non-delivery.
    """

    __tablename__ = "milestone_events"
    __table_args__ = (UniqueConstraint("work_date", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
