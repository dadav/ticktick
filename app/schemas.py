from datetime import datetime
from pydantic import BaseModel


class SessionInfo(BaseModel):
    id: int
    start_time: datetime
    current_time: datetime
    net_work_seconds: int
    net_work_formatted: str
    pause_count: int
    total_pause_seconds: int


class DailySummary(BaseModel):
    date: str
    start_time: datetime
    end_time: datetime
    session_count: int
    actual_work_seconds: int
    actual_work_formatted: str
    credited_work_seconds: int
    credited_work_formatted: str
    pause_count: int
    total_pause_seconds: int
    total_pause_formatted: str
    lunch_deduction_seconds: int
    lunch_deduction_formatted: str
    lunch_break_applies: bool
    remaining_for_earliest_seconds: int
    remaining_for_daily_seconds: int
    remaining_for_daily: str
    remaining_for_max_seconds: int
    remaining_for_max: str
    overtime_seconds: int
    overtime_formatted: str
    target_reached: bool
    cap_reached: bool
    cap_exceeded: bool


class Calculations(BaseModel):
    lunch_break_applies: bool
    lunch_break_at: str | None
    earliest_leave: str | None
    normal_leave: str | None
    latest_leave: str | None
    earliest_reached: bool = False
    remaining_for_daily: str
    overtime_seconds: int
    overtime_formatted: str


class StatusResponse(BaseModel):
    status: str  # "idle", "running", "paused"
    session: SessionInfo | None
    calculations: Calculations | None
    auto_stopped: bool = False
    day: DailySummary | None = None
    can_start: bool = True
    start_blocked_reason: str | None = None


class ActionResponse(BaseModel):
    success: bool
    message: str
    status: str


class SessionSummary(BaseModel):
    id: int
    date: str
    start_time: str
    end_time: str | None
    net_work_formatted: str
    overtime_seconds: int
    overtime_formatted: str
    status: str


class WeekSummary(BaseModel):
    total_seconds: int
    total_formatted: str
    target_seconds: int
    target_formatted: str
    days_worked: int
    avg_per_day_formatted: str
    overtime_seconds: int
    overtime_formatted: str
    average_start_time: str | None  # HH:MM format
    average_end_time: str | None  # HH:MM format


class MonthSummary(BaseModel):
    total_seconds: int
    total_formatted: str
    days_worked: int
    avg_per_day_formatted: str
    overtime_seconds: int
    overtime_formatted: str
    average_start_time: str | None  # HH:MM format
    average_end_time: str | None  # HH:MM format


class DayHistory(BaseModel):
    day: DailySummary
    sessions: list[SessionSummary]


class StatisticsResponse(BaseModel):
    this_week: WeekSummary
    this_month: MonthSummary
    recent_sessions: list[SessionSummary]
    recent_days: list[DayHistory]


class PausePeriodInfo(BaseModel):
    id: int
    pause_start: str  # HH:MM format
    pause_end: str | None  # HH:MM format, None if pause is still active
    duration_formatted: str  # Duration as formatted string


class SessionUpdateRequest(BaseModel):
    start_time: str | None = None  # HH:MM format
    end_time: str | None = None  # HH:MM format


class SessionCreateRequest(BaseModel):
    date: str  # YYYY-MM-DD format
    start_time: str  # HH:MM format
    end_time: str  # HH:MM format


class SessionDetailResponse(BaseModel):
    id: int
    date: str
    start_time: str  # HH:MM format
    end_time: str | None  # HH:MM format
    net_work_formatted: str
    gross_work_formatted: str
    total_pause_formatted: str
    overtime_seconds: int
    overtime_formatted: str
    status: str
    pause_count: int
    pauses: list[PausePeriodInfo]
