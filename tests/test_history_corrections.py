import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import WorkSession
from app.services import calculations, timer


class HistoryCorrectionsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.origin = datetime(2026, 9, 15)
        self.clock_patch = patch("app.services.timer.datetime", wraps=datetime)
        self.clock = self.clock_patch.start()
        self.clock.now.return_value = self.origin + timedelta(hours=12)

    def tearDown(self):
        self.clock_patch.stop()
        self.db.close()
        self.engine.dispose()
        self.directory.cleanup()

    def at(self, hour, action, *args):
        self.clock.now.return_value = self.origin + timedelta(hours=hour)
        return action(self.db, *args)

    def earlier(self, end):
        self.at(12, timer.create_manual_session, "2026-09-15", "00:00", end)
        return self.db.query(WorkSession).one()

    def assert_preserved(self, active, hour, seconds, response):
        status = self.at(hour, timer.get_status)
        self.db.refresh(active)
        self.assertEqual(active.end_time, self.origin + timedelta(hours=hour))
        self.assertEqual(active.net_seconds, seconds)
        self.assertEqual(response.status, "idle")
        self.assertEqual(status.status, "idle")
        self.assertTrue(status.day.cap_exceeded)

    def test_insert_cannot_erase_active_session(self):
        self.at(12, timer.start_timer)
        active = timer.get_active_session(self.db)
        result = self.at(
            13, timer.create_manual_session, "2026-09-15", "00:00", "10:30"
        )
        self.assert_preserved(active, 13, 3600, result)

    def test_edit_cannot_erase_active_session(self):
        earlier = self.earlier("08:00")
        self.at(12, timer.start_timer)
        active = timer.get_active_session(self.db)
        result = self.at(13, timer.update_session, earlier.id, None, "10:30")
        self.assert_preserved(active, 13, 3600, result)

    def test_insert_cannot_truncate_part_of_active_session(self):
        self.at(12, timer.start_timer)
        active = timer.get_active_session(self.db)
        self.assertEqual(
            self.at(16, timer.get_status).session.net_work_seconds, 4 * 3600
        )
        result = self.at(
            16, timer.create_manual_session, "2026-09-15", "00:00", "08:00"
        )
        self.assert_preserved(active, 16, 4 * 3600, result)

    def test_edit_cannot_truncate_part_of_active_session(self):
        earlier = self.earlier("06:00")
        self.at(12, timer.start_timer)
        active = timer.get_active_session(self.db)
        result = self.at(15.5, timer.update_session, earlier.id, None, "08:00")
        self.assert_preserved(active, 15.5, int(3.5 * 3600), result)

    def test_correction_preserves_closed_and_open_pauses(self):
        self.at(12, timer.start_timer)
        active = timer.get_active_session(self.db)
        self.at(12.25, timer.pause_timer)
        self.at(12.5, timer.continue_timer)
        self.at(13, timer.pause_timer)
        result = self.at(
            13.5, timer.create_manual_session, "2026-09-15", "00:00", "10:30"
        )
        self.assert_preserved(active, 13.5, 45 * 60, result)
        self.assertEqual(
            [(p.pause_start, p.pause_end) for p in active.pause_periods],
            [
                (
                    self.origin + timedelta(hours=12.25),
                    self.origin + timedelta(hours=12.5),
                ),
                (
                    self.origin + timedelta(hours=13),
                    self.origin + timedelta(hours=13.5),
                ),
            ],
        )

    def test_existing_cap_is_enforced_before_correction(self):
        self.at(6, timer.start_timer)
        active = timer.get_active_session(self.db)
        self.at(18, timer.create_manual_session, "2026-09-15", "00:00", "01:00")
        self.at(18, timer.get_status)
        self.db.refresh(active)
        self.assertEqual(active.end_time, self.origin + timedelta(hours=16.5))
        self.assertEqual(active.net_seconds, int(10.5 * 3600))

    def test_correction_preserves_work_after_cap_crossing_then_lunch_drop(self):
        with patch.object(calculations, "MAX_DAILY_SECONDS", 6 * 3600):
            self.at(12, timer.start_timer)
            active = timer.get_active_session(self.db)
            result = self.at(
                16.25, timer.create_manual_session, "2026-09-15", "10:00", "12:00"
            )
            self.assertEqual(result.status, "idle")
            status = self.at(16.25, timer.get_status)
            self.db.refresh(active)
            self.assertEqual(active.end_time, self.origin + timedelta(hours=16.25))
            self.assertEqual(active.net_seconds, int(4.25 * 3600))
            self.assertEqual(status.day.credited_work_seconds, int(5.75 * 3600))

    def test_out_of_band_history_cannot_erase_active_session_or_pauses(self):
        self.at(12, timer.start_timer)
        active = timer.get_active_session(self.db)
        self.at(12.25, timer.pause_timer)
        self.db.add(
            WorkSession(
                date=self.origin.date(),
                start_time=self.origin,
                end_time=self.origin + timedelta(hours=10.5),
                status="completed",
            )
        )
        self.db.commit()
        with self.assertLogs("app.services.timer", level="WARNING") as logs:
            status = self.at(13, timer.get_status)
        self.db.refresh(active)
        self.assertTrue(status.auto_stopped)
        self.assertEqual(active.end_time, self.origin + timedelta(hours=13))
        self.assertEqual(active.net_seconds, 15 * 60)
        self.assertEqual(
            active.pause_periods[0].pause_start,
            self.origin + timedelta(hours=12.25),
        )
        self.assertEqual(active.pause_periods[0].pause_end, active.end_time)
        self.assertEqual(logs.records[0].session_id, active.id)
        self.assertEqual(logs.records[0].capped_end, active.start_time.isoformat())

    def test_correction_below_cap_keeps_timer_running(self):
        self.at(12, timer.start_timer)
        result = self.at(
            13, timer.create_manual_session, "2026-09-15", "06:00", "08:00"
        )
        self.assertEqual(result.status, "running")
        self.assertEqual(
            self.at(13, timer.get_status).day.actual_work_seconds, 3 * 3600
        )

    def test_other_day_correction_does_not_stop_active_timer(self):
        self.at(12, timer.start_timer)
        result = self.at(
            13, timer.create_manual_session, "2026-09-14", "00:00", "11:00"
        )
        self.assertEqual(result.status, "running")
        self.assertEqual(self.at(13, timer.get_status).day.actual_work_seconds, 3600)

    def test_edit_rejects_future_end_without_mutating_history(self):
        earlier = self.earlier("08:00")
        with self.assertRaises(HTTPException) as error:
            self.at(12, timer.update_session, earlier.id, None, "13:00")
        self.assertEqual(error.exception.status_code, 422)
        self.db.refresh(earlier)
        self.assertEqual(earlier.end_time, self.origin + timedelta(hours=8))

    def test_history_and_stop_are_committed_together(self):
        self.at(12, timer.start_timer)
        active_id = timer.get_active_session(self.db).id
        snapshots = []

        def before_commit(session):
            with self.Session() as observer:
                snapshots.append(
                    (
                        observer.query(WorkSession).count(),
                        observer.get(WorkSession, active_id).end_time,
                    )
                )

        event.listen(self.db, "before_commit", before_commit)
        result = self.at(
            16, timer.create_manual_session, "2026-09-15", "00:00", "08:00"
        )
        event.remove(self.db, "before_commit", before_commit)
        self.assertEqual(result.status, "idle")
        self.assertEqual(snapshots, [(1, None)])
        with self.Session() as observer:
            self.assertEqual(observer.query(WorkSession).count(), 2)
            self.assertEqual(
                observer.get(WorkSession, active_id).end_time,
                self.origin + timedelta(hours=16),
            )

    def test_failed_stop_rolls_back_history_correction(self):
        self.at(12, timer.start_timer)
        with patch.object(
            timer, "_finish_session", side_effect=RuntimeError("stop failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "stop failed"):
                self.at(16, timer.create_manual_session, "2026-09-15", "00:00", "08:00")
        with self.Session() as observer:
            self.assertEqual(observer.query(WorkSession).count(), 1)
            self.assertEqual(timer.get_current_status(observer), "running")
            self.assertIsNone(observer.query(WorkSession).one().end_time)

    def test_poll_waits_for_history_correction_and_keeps_saved_work(self):
        self.at(12, timer.start_timer)
        self.clock.now.return_value = self.origin + timedelta(hours=16)
        inserted = threading.Event()
        poll_started = threading.Event()
        release_writer = threading.Event()

        def after_statement(connection, cursor, statement, parameters, context, many):
            if threading.current_thread().name.startswith(
                "history-writer"
            ) and statement.startswith("INSERT INTO work_sessions"):
                inserted.set()
                if not release_writer.wait(5):
                    raise RuntimeError("Writer was not released")

        def before_statement(connection, cursor, statement, parameters, context, many):
            if (
                threading.current_thread().name.startswith("history-poll")
                and statement == "BEGIN IMMEDIATE"
            ):
                poll_started.set()

        def write():
            with self.Session() as db:
                return timer.create_manual_session(db, "2026-09-15", "00:00", "08:00")

        def poll():
            with self.Session() as db:
                return timer.get_status(db)

        event.listen(self.engine, "after_cursor_execute", after_statement)
        event.listen(self.engine, "before_cursor_execute", before_statement)
        with (
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="history-writer"
            ) as writer_pool,
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="history-poll"
            ) as poll_pool,
        ):
            writer = writer_pool.submit(write)
            try:
                self.assertTrue(inserted.wait(3))
                reader = poll_pool.submit(poll)
                self.assertTrue(poll_started.wait(3))
                self.assertFalse(reader.done())
            finally:
                release_writer.set()
            self.assertEqual(writer.result(timeout=5).status, "idle")
            status = reader.result(timeout=5)
            self.assertEqual(status.status, "idle")
            self.assertTrue(status.day.cap_exceeded)
            self.assertEqual(status.day.credited_work_seconds, 12 * 3600)
        with self.Session() as observer:
            active = (
                observer.query(WorkSession)
                .filter(WorkSession.start_time == self.origin + timedelta(hours=12))
                .one()
            )
            self.assertEqual(active.net_seconds, 4 * 3600)
