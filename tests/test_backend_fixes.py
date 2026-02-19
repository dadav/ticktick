import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import TimerState, WorkSession, PausePeriod
from app.database import Base
from app.routers import api
from app.services import statistics, timer


class BackendFixesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        self.tmp_dir.cleanup()

    def test_start_timer_race_discards_losing_session(self) -> None:
        state = TimerState(
            id=1, is_running=False, is_paused=False, current_session_id=None
        )
        self.db.add(state)
        self.db.commit()

        with patch("sqlalchemy.orm.query.Query.update", autospec=True, return_value=0):
            result = timer.start_timer(self.db)

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Timer already running")

        sessions = self.db.query(WorkSession).all()
        self.assertEqual(sessions, [])

    def test_statistics_ignore_future_completed_sessions(self) -> None:
        now = datetime.now()
        tomorrow = now + timedelta(days=1)

        today_session = WorkSession(
            date=now.date(),
            start_time=now.replace(hour=9, minute=0, second=0, microsecond=0),
            end_time=now.replace(hour=10, minute=0, second=0, microsecond=0),
            net_seconds=3600,
            status="completed",
        )
        future_session = WorkSession(
            date=tomorrow.date(),
            start_time=tomorrow.replace(hour=9, minute=0, second=0, microsecond=0),
            end_time=tomorrow.replace(hour=11, minute=0, second=0, microsecond=0),
            net_seconds=7200,
            status="completed",
        )
        self.db.add_all([today_session, future_session])
        self.db.commit()

        summary = statistics.get_statistics(self.db)

        self.assertEqual(summary.this_week.total_seconds, 3600)
        self.assertEqual(summary.this_month.total_seconds, 3600)
        self.assertEqual(len(summary.recent_sessions), 2)

    def test_session_details_raises_http_404_when_not_found(self) -> None:
        with self.assertRaises(HTTPException) as context:
            api.get_session_details(session_id=999, db=self.db)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Session not found")

    def test_session_details_use_live_time_for_active_session(self) -> None:
        base_now = datetime(2026, 2, 18, 10, 0, 0)

        with patch("app.services.timer.datetime") as timer_datetime:
            timer_datetime.now.return_value = base_now
            result = timer.start_timer(self.db)

        self.assertTrue(result.success)
        active_session = timer.get_active_session(self.db)
        self.assertIsNotNone(active_session)

        with patch("app.services.statistics.datetime") as statistics_datetime:
            statistics_datetime.now.return_value = base_now + timedelta(
                hours=1, minutes=5
            )
            details = statistics.get_session_details(self.db, active_session.id)

        self.assertIsNotNone(details)
        self.assertEqual(details.net_work_formatted, "01:05")
        self.assertEqual(details.gross_work_formatted, "01:05")
        self.assertEqual(details.overtime_formatted, "-07:07")

    def test_auto_stop_at_max_daily_hours(self) -> None:
        """Timer auto-stops when net work reaches MAX_DAILY_HOURS."""
        base_now = datetime(2026, 2, 19, 7, 0, 0)

        with patch("app.services.timer.datetime") as timer_datetime:
            timer_datetime.now.return_value = base_now
            result = timer.start_timer(self.db)
        self.assertTrue(result.success)

        # Advance time by 10h + 30min (net 10h since no pauses, plus buffer)
        later = base_now + timedelta(hours=10, minutes=1)

        with (
            patch("app.services.timer.datetime") as timer_datetime,
            patch(
                "app.services.calculations.datetime",
                wraps=datetime,
            ) as calc_datetime,
        ):
            timer_datetime.now.return_value = later
            calc_datetime.now.return_value = later
            status = timer.get_status(self.db)

        self.assertEqual(status.status, "idle")
        self.assertTrue(status.auto_stopped)

        # Session should be completed
        session = self.db.query(WorkSession).first()
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.net_seconds, 10 * 3600)

    def test_net_work_seconds_capped_at_max(self) -> None:
        """calculate_net_work_seconds never exceeds MAX_DAILY_SECONDS."""
        from app.services.calculations import calculate_net_work_seconds
        from app.config import MAX_DAILY_SECONDS

        now = datetime(2026, 2, 19, 7, 0, 0)
        session = WorkSession(
            date=now.date(),
            start_time=now,
            status="active",
        )
        # 12 hours later, well past the 10h cap
        later = now + timedelta(hours=12)
        result = calculate_net_work_seconds(session, later)
        self.assertEqual(result, MAX_DAILY_SECONDS)

    def test_delete_session_returns_running_status_when_timer_active(self) -> None:
        start_result = timer.start_timer(self.db)
        self.assertTrue(start_result.success)

        now = datetime.now().replace(second=0, microsecond=0)
        completed_session = WorkSession(
            date=now.date(),
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
            net_seconds=3600,
            status="completed",
        )
        self.db.add(completed_session)
        self.db.commit()
        self.db.refresh(completed_session)

        delete_result = timer.delete_session(self.db, completed_session.id)

        self.assertTrue(delete_result.success)
        self.assertEqual(delete_result.status, "running")


    def test_update_session_changes_end_time_and_recalculates(self) -> None:
        """update_session recalculates net_seconds after changing end_time."""
        now = datetime(2026, 2, 19, 8, 0, 0)
        session = WorkSession(
            date=now.date(),
            start_time=now,
            end_time=now.replace(hour=16, minute=0),
            net_seconds=8 * 3600,
            status="completed",
        )
        self.db.add(session)
        state = TimerState(id=1, is_running=False, is_paused=False, current_session_id=None)
        self.db.add(state)
        self.db.commit()
        self.db.refresh(session)

        result = timer.update_session(self.db, session.id, None, "17:00")
        self.assertTrue(result.success)

        self.db.refresh(session)
        self.assertEqual(session.end_time, datetime(2026, 2, 19, 17, 0))
        self.assertEqual(session.net_seconds, 9 * 3600)

    def test_update_session_blocks_active_session(self) -> None:
        """update_session refuses to edit the currently active session."""
        now = datetime(2026, 2, 19, 8, 0, 0)
        session = WorkSession(
            date=now.date(),
            start_time=now,
            status="active",
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)

        state = TimerState(id=1, is_running=True, is_paused=False, current_session_id=session.id)
        self.db.add(state)
        self.db.commit()

        result = timer.update_session(self.db, session.id, "09:00", None)
        self.assertFalse(result.success)
        self.assertIn("active", result.message.lower())

    def test_update_session_validates_start_before_end(self) -> None:
        """update_session rejects start >= end."""
        now = datetime(2026, 2, 19, 8, 0, 0)
        session = WorkSession(
            date=now.date(),
            start_time=now,
            end_time=now.replace(hour=16, minute=0),
            net_seconds=8 * 3600,
            status="completed",
        )
        self.db.add(session)
        state = TimerState(id=1, is_running=False, is_paused=False, current_session_id=None)
        self.db.add(state)
        self.db.commit()
        self.db.refresh(session)

        result = timer.update_session(self.db, session.id, "17:00", "16:00")
        self.assertFalse(result.success)
        self.assertIn("before", result.message.lower())

    def test_update_session_validates_against_pauses(self) -> None:
        """update_session rejects end_time before last pause end."""
        now = datetime(2026, 2, 19, 8, 0, 0)
        session = WorkSession(
            date=now.date(),
            start_time=now,
            end_time=now.replace(hour=16, minute=0),
            net_seconds=7 * 3600,
            status="completed",
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)

        pause = PausePeriod(
            session_id=session.id,
            pause_start=datetime(2026, 2, 19, 12, 0, 0),
            pause_end=datetime(2026, 2, 19, 13, 0, 0),
        )
        self.db.add(pause)
        state = TimerState(id=1, is_running=False, is_paused=False, current_session_id=None)
        self.db.add(state)
        self.db.commit()

        # Try setting end_time before the pause ends
        result = timer.update_session(self.db, session.id, None, "12:30")
        self.assertFalse(result.success)
        self.assertIn("pause", result.message.lower())

    def test_update_session_not_found(self) -> None:
        """update_session returns failure for non-existent session."""
        state = TimerState(id=1, is_running=False, is_paused=False, current_session_id=None)
        self.db.add(state)
        self.db.commit()

        result = timer.update_session(self.db, 999, "09:00", "17:00")
        self.assertFalse(result.success)
        self.assertIn("not found", result.message.lower())


if __name__ == "__main__":
    unittest.main()
