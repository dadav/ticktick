import asyncio
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from app.database import Base
from app.models import (
    MilestoneEvent,
    MilestoneSettings,
    NotificationSettings,
    PausePeriod,
    TimerState,
    WorkSession,
)
from app.routers import api
from app.schemas import NotificationSettingsUpdate
from app.services import gotify, notifications, timer

MONDAY = date(2026, 10, 12)
SATURDAY = date(2026, 10, 17)
CHRISTMAS = date(2026, 12, 25)  # Friday, public holiday in DE


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute))


class NotificationTestCase(unittest.TestCase):
    """Pins reminder config so local .env values cannot change the outcome."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.tmp_dir.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()
        config_patches = {
            "REMINDER_TIME": time(9, 0),
            "REMINDER_CUTOFF": time(12, 0),
            "REMINDER_WEEKDAYS": frozenset({0, 1, 2, 3, 4}),
            "HOLIDAY_COUNTRY": "DE",
            "HOLIDAY_SUBDIV": None,
        }
        for name, value in config_patches.items():
            patcher = patch.object(notifications, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        notifications._holiday_calendar.cache_clear()
        self.addCleanup(notifications._holiday_calendar.cache_clear)
        sender = patch.object(gotify, "send_message")
        self.send = sender.start()
        self.addCleanup(sender.stop)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        self.tmp_dir.cleanup()

    def settings(self) -> NotificationSettings:
        with self.SessionLocal() as observer:
            return observer.get(NotificationSettings, 1)

    def add_session(self, day: date, status: str) -> None:
        self.db.add(
            WorkSession(
                date=day,
                start_time=at(day, 7),
                end_time=at(day, 8),
                status=status,
            )
        )
        self.db.commit()


class WorkdayTest(NotificationTestCase):
    def test_monday_is_workday(self) -> None:
        self.assertTrue(notifications.is_workday(MONDAY))

    def test_saturday_is_not_workday(self) -> None:
        self.assertFalse(notifications.is_workday(SATURDAY))

    def test_public_holiday_is_not_workday(self) -> None:
        self.assertFalse(notifications.is_workday(CHRISTMAS))
        self.assertEqual(notifications.holiday_name(CHRISTMAS), "Erster Weihnachtstag")


class SkipReasonTest(NotificationTestCase):
    def reason(self, now: datetime, **overrides) -> str | None:
        settings = NotificationSettings(
            enabled=overrides.pop("enabled", True),
            paused_until=overrides.pop("paused_until", None),
            last_start_reminder_date=overrides.pop("last_sent", None),
        )
        return notifications.start_reminder_skip_reason(
            settings,
            now,
            has_day_session=overrides.pop("has_day_session", False),
            timer_idle=overrides.pop("timer_idle", True),
        )

    def test_due_inside_window(self) -> None:
        self.assertIsNone(self.reason(at(MONDAY, 9)))
        self.assertIsNone(self.reason(at(MONDAY, 11, 59)))

    def test_skip_reasons(self) -> None:
        cases = {
            "disabled": (at(MONDAY, 10), {"enabled": False}),
            "paused": (at(MONDAY, 10), {"paused_until": MONDAY}),
            "not_workday": (at(SATURDAY, 10), {}),
            "too_early": (at(MONDAY, 8, 59), {}),
            "past_cutoff": (at(MONDAY, 12), {}),
            "already_sent": (at(MONDAY, 10), {"last_sent": MONDAY}),
            "timer_active": (at(MONDAY, 10), {"timer_idle": False}),
            "session_exists": (at(MONDAY, 10), {"has_day_session": True}),
        }
        for expected, (now, overrides) in cases.items():
            with self.subTest(expected):
                self.assertEqual(self.reason(now, **overrides), expected)

    def test_pause_ends_after_paused_until(self) -> None:
        self.assertIsNone(self.reason(at(MONDAY, 10), paused_until=date(2026, 10, 11)))


class CheckStartReminderTest(NotificationTestCase):
    def test_sends_once_per_day(self) -> None:
        self.assertTrue(notifications.check_start_reminder(self.db, at(MONDAY, 9)))
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 9, 1)))
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.settings().last_start_reminder_date, MONDAY)

    def test_reset_session_does_not_count_as_started(self) -> None:
        self.add_session(MONDAY, "reset")
        self.assertTrue(notifications.check_start_reminder(self.db, at(MONDAY, 10)))

    def test_completed_session_today_skips(self) -> None:
        self.add_session(MONDAY, "completed")
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 10)))
        self.send.assert_not_called()

    def test_running_overnight_session_skips(self) -> None:
        self.add_session(date(2026, 10, 11), "active")
        self.db.add(TimerState(id=1, current_session_id=1, is_running=True))
        self.db.commit()
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 10)))
        self.send.assert_not_called()

    def test_retryable_failure_releases_claim(self) -> None:
        self.send.side_effect = gotify.GotifyError("refused", retryable=True)
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 9)))
        self.assertIsNone(self.settings().last_start_reminder_date)

        self.send.side_effect = None
        self.assertTrue(notifications.check_start_reminder(self.db, at(MONDAY, 9, 1)))
        self.assertEqual(self.send.call_count, 2)

    def test_ambiguous_failure_keeps_claim_to_avoid_duplicates(self) -> None:
        self.send.side_effect = gotify.GotifyError("timed out", retryable=False)
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 9)))
        self.assertEqual(self.settings().last_start_reminder_date, MONDAY)

        self.send.side_effect = None
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 9, 1)))
        self.assertEqual(self.send.call_count, 1)

    def test_crash_after_claim_does_not_resend(self) -> None:
        # A claim committed before a crash means the day counts as sent (at-most-once).
        self.db.add(NotificationSettings(id=1, last_start_reminder_date=MONDAY))
        self.db.commit()
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 10)))
        self.send.assert_not_called()

    def test_catch_up_inside_window_only(self) -> None:
        self.assertTrue(notifications.check_start_reminder(self.db, at(MONDAY, 11, 30)))
        tuesday = date(2026, 10, 13)
        self.assertFalse(notifications.check_start_reminder(self.db, at(tuesday, 12)))
        self.assertEqual(self.send.call_count, 1)

    def test_paused_then_resumes_after_date(self) -> None:
        notifications.update_settings(self.db, True, MONDAY, at(MONDAY, 8))
        self.assertFalse(notifications.check_start_reminder(self.db, at(MONDAY, 10)))
        tuesday = date(2026, 10, 13)
        self.assertTrue(notifications.check_start_reminder(self.db, at(tuesday, 10)))


class SettingsTest(NotificationTestCase):
    def test_past_pause_date_is_normalized(self) -> None:
        result = notifications.update_settings(
            self.db, True, date(2026, 10, 1), at(MONDAY, 8)
        )
        self.assertIsNone(result.paused_until)
        self.assertIsNone(self.settings().paused_until)

    def test_expired_pause_does_not_block_toggle(self) -> None:
        notifications.update_settings(self.db, True, MONDAY, at(MONDAY, 8))
        tuesday = date(2026, 10, 13)
        self.assertIsNone(
            notifications.get_settings(self.db, at(tuesday, 8)).paused_until
        )

        # A stale page resubmits the expired date while toggling off.
        result = notifications.update_settings(self.db, False, MONDAY, at(tuesday, 8))
        self.assertFalse(result.enabled)
        self.assertIsNone(result.paused_until)

    def test_api_roundtrip(self) -> None:
        with patch("app.routers.api.datetime") as clock:
            clock.now.return_value = at(MONDAY, 8)
            body = NotificationSettingsUpdate(
                enabled=True, paused_until=date(2026, 10, 20)
            )
            updated = api.update_notification_settings(body, self.db)
            loaded = api.get_notification_settings(self.db)
        self.assertEqual(updated.paused_until, date(2026, 10, 20))
        self.assertEqual(loaded.paused_until, date(2026, 10, 20))
        self.assertEqual(loaded.reminder_time, "09:00")
        self.assertTrue(loaded.today_is_workday)

    def test_test_endpoint_requires_configuration(self) -> None:
        with (
            patch.object(api, "GOTIFY_CONFIGURED", False),
            self.assertRaises(HTTPException) as raised,
        ):
            api.send_test_notification()
        self.assertEqual(raised.exception.status_code, 409)
        self.send.assert_not_called()

    def test_test_endpoint_maps_gotify_failure(self) -> None:
        self.send.side_effect = gotify.GotifyError("HTTP 401", retryable=False)
        with (
            patch.object(api, "GOTIFY_CONFIGURED", True),
            self.assertRaises(HTTPException) as raised,
        ):
            api.send_test_notification()
        self.assertEqual(raised.exception.status_code, 502)


class GotifyClientTest(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in {
            "GOTIFY_URL": "http://gotify.test",
            "GOTIFY_TOKEN": "t",
        }.items():
            patcher = patch.object(gotify, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def send_with(self, error: Exception) -> gotify.GotifyError:
        with (
            patch.object(gotify, "urlopen", side_effect=error),
            self.assertRaises(gotify.GotifyError) as raised,
        ):
            gotify.send_message("title", "message")
        return raised.exception

    def http_error(self, code: int) -> HTTPError:
        return HTTPError("http://gotify.test/message", code, "error", {}, None)

    def test_retry_classification(self) -> None:
        cases = {
            "server error": (self.http_error(500), True),
            "rate limited": (self.http_error(429), True),
            "bad token": (self.http_error(401), False),
            "refused": (URLError(ConnectionRefusedError()), True),
            "dns": (URLError("Name or service not known"), True),
            "url timeout": (URLError(TimeoutError()), False),
            "read timeout": (TimeoutError(), False),
            "reset": (ConnectionResetError(), False),
        }
        for name, (error, retryable) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.send_with(error).retryable, retryable)

    def test_request_shape(self) -> None:
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        with patch.object(gotify, "urlopen", return_value=response) as urlopen:
            gotify.send_message("title", "message", priority=7)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://gotify.test/message")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-gotify-key"), "t")
        self.assertIn(b'"priority": 7', request.data)

    def test_unconfigured_raises(self) -> None:
        with (
            patch.object(gotify, "GOTIFY_URL", ""),
            self.assertRaises(gotify.GotifyError),
        ):
            gotify.send_message("title", "message")


TUESDAY = date(2026, 10, 13)


def at_seconds(day: date, hour: int, minute: int, second: int) -> datetime:
    return datetime.combine(day, time(hour, minute, second))


class MilestoneTestCase(NotificationTestCase):
    def start(
        self, start: datetime, pauses: list[tuple[datetime, datetime]] = ()
    ) -> None:
        session = WorkSession(date=start.date(), start_time=start, status="active")
        session.pause_periods = [
            PausePeriod(pause_start=begin, pause_end=end) for begin, end in pauses
        ]
        self.db.add(session)
        self.db.flush()
        state = self.db.get(TimerState, 1) or TimerState(id=1)
        state.current_session_id = session.id
        state.is_running = True
        state.is_paused = False
        self.db.add(state)
        self.db.commit()

    def completed(self, start: datetime, end: datetime) -> None:
        self.db.add(
            WorkSession(
                date=start.date(), start_time=start, end_time=end, status="completed"
            )
        )
        self.db.commit()

    def configure(self, now: datetime, paused_until: date | None = None, **flags):
        notifications.update_settings(
            self.db,
            True,
            paused_until,
            now,
            notify_min_work=flags.get("min_work", False),
            notify_daily_target=flags.get("daily_target", False),
            notify_daily_max=flags.get("daily_max", False),
        )

    def check(self, now: datetime) -> list[tuple[date, str]]:
        sent = notifications.check_milestones(self.db, now)
        return [(milestone.work_date, milestone.key) for milestone in sent]

    def outcomes(self) -> dict[tuple[date, str], str]:
        with self.SessionLocal() as observer:
            return {
                (row.work_date, row.key): row.outcome
                for row in observer.query(MilestoneEvent)
            }

    def timer_at(self, now: datetime, action):
        with patch("app.services.timer.datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            return action(self.db)

    def active_session_id(self) -> int | None:
        with self.SessionLocal() as observer:
            return observer.get(TimerState, 1).current_session_id


class MilestoneThresholdTest(MilestoneTestCase):
    def test_target_and_max_move_without_lunch_break(self) -> None:
        self.configure(at(MONDAY, 8), daily_target=True, daily_max=True)
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at(MONDAY, 16, 41)), [])
        self.assertEqual(self.check(at(MONDAY, 16, 42)), [(MONDAY, "daily_target")])
        self.assertEqual(self.check(at(MONDAY, 18, 29)), [])
        self.assertEqual(self.check(at(MONDAY, 18, 30)), [(MONDAY, "daily_max")])

    def test_breaks_credit_toward_lunch(self) -> None:
        # Actual work needed for 8h12 credited: 8h12 + max(0, 30 min - breaks).
        for offset, pause_minutes in enumerate([15, 30, 60]):
            with self.subTest(pause_minutes=pause_minutes):
                day = MONDAY + timedelta(days=3 * offset)
                pause = (at(day, 12), at(day, 12) + timedelta(minutes=pause_minutes))
                self.configure(at(day, 8), daily_target=True)
                self.start(at(day, 8), pauses=[pause])
                needed = timedelta(hours=8, minutes=12 + max(0, 30 - pause_minutes))
                reached = at(day, 8) + needed + timedelta(minutes=pause_minutes)
                before = reached - timedelta(minutes=1)
                self.assertEqual(self.check(before), [])
                self.assertEqual(self.check(reached), [(day, "daily_target")])
                self.timer_at(reached, timer.stop_timer)

    def test_min_work_uses_actual_work_after_credit_drops(self) -> None:
        self.configure(at(MONDAY, 8), min_work=True)
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at_seconds(MONDAY, 13, 59, 45)), [])
        # Credited work is 5h30m45s here, actual work is past 6h.
        self.assertEqual(
            self.check(at_seconds(MONDAY, 14, 0, 45)), [(MONDAY, "min_work")]
        )

    def test_loop_enforces_cap_without_polling(self) -> None:
        self.configure(at(MONDAY, 8), daily_max=True)
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at(MONDAY, 19)), [(MONDAY, "daily_max")])
        self.assertIsNone(self.active_session_id())
        with self.SessionLocal() as observer:
            session = observer.query(WorkSession).one()
            self.assertEqual(session.end_time, at(MONDAY, 18, 30))
            self.assertEqual(session.status, "completed")


class MilestoneDeliveryTest(MilestoneTestCase):
    def test_sends_once_per_workday(self) -> None:
        self.configure(at(MONDAY, 8), min_work=True)
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at(MONDAY, 14, 1)), [(MONDAY, "min_work")])
        self.assertEqual(self.check(at(MONDAY, 14, 2)), [])
        self.timer_at(at(MONDAY, 15), timer.stop_timer)

        self.start(at(TUESDAY, 8))
        self.assertEqual(self.check(at(TUESDAY, 14, 1)), [(TUESDAY, "min_work")])
        self.assertEqual(self.send.call_count, 2)

    def test_overnight_session_stopped_by_poll_before_check(self) -> None:
        self.configure(at(MONDAY, 15), daily_max=True)
        self.start(at(MONDAY, 15))
        status = self.timer_at(at_seconds(TUESDAY, 1, 30, 5), timer.get_status)
        self.assertTrue(status.auto_stopped)

        sent = self.check(at_seconds(TUESDAY, 1, 30, 40))
        self.assertEqual(sent, [(MONDAY, "daily_max")])
        title, message = self.send.call_args.args
        self.assertEqual(title, "TickTick: Tagesmaximum erreicht")
        self.assertIn("(Arbeitstag 12.10.2026)", message)

    def test_user_stops_right_after_crossing(self) -> None:
        self.configure(at(MONDAY, 8), daily_target=True)
        self.start(at(MONDAY, 8))
        self.timer_at(at(MONDAY, 16, 43), timer.stop_timer)
        self.assertEqual(
            self.check(at_seconds(MONDAY, 16, 43, 40)), [(MONDAY, "daily_target")]
        )

    def test_overnight_retryable_failure_retries_start_day(self) -> None:
        self.configure(at(MONDAY, 15), daily_max=True)
        self.start(at(MONDAY, 15))
        self.send.side_effect = [gotify.GotifyError("refused", retryable=True), None]
        self.assertEqual(self.check(at(TUESDAY, 1, 31)), [])
        self.assertIsNone(self.active_session_id())
        self.assertNotIn((MONDAY, "daily_max"), self.outcomes())

        self.assertEqual(self.check(at(TUESDAY, 1, 32)), [(MONDAY, "daily_max")])
        self.assertEqual(self.check(at(TUESDAY, 1, 33)), [])
        self.assertEqual(self.send.call_count, 2)

    def test_ambiguous_failure_keeps_claim(self) -> None:
        self.configure(at(MONDAY, 8), daily_target=True)
        self.start(at(MONDAY, 8))
        self.send.side_effect = gotify.GotifyError("timed out", retryable=False)
        self.assertEqual(self.check(at(MONDAY, 16, 43)), [])
        self.send.side_effect = None
        self.assertEqual(self.check(at(MONDAY, 16, 44)), [])
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.outcomes()[(MONDAY, "daily_target")], "claimed")

    def test_check_once_isolates_failures(self) -> None:
        with (
            patch.object(notifications, "SessionLocal", self.SessionLocal),
            patch.object(
                notifications, "check_start_reminder", side_effect=RuntimeError("boom")
            ),
            patch.object(notifications, "check_milestones") as milestones,
            self.assertLogs(notifications.logger, "ERROR"),
        ):
            notifications._check_once()
        milestones.assert_called_once()

    def test_old_events_are_pruned(self) -> None:
        old_day = MONDAY - timedelta(days=8)
        self.db.add(
            MilestoneEvent(
                work_date=old_day,
                key="min_work",
                outcome="claimed",
                recorded_at=at(old_day, 14),
            )
        )
        self.db.commit()
        self.check(at(MONDAY, 8))
        self.assertNotIn((old_day, "min_work"), self.outcomes())


class MilestoneSuppressionTest(MilestoneTestCase):
    def test_enabling_after_unobserved_crossing_does_not_send(self) -> None:
        self.configure(at(MONDAY, 8))
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at_seconds(MONDAY, 13, 59, 45)), [])
        self.configure(at_seconds(MONDAY, 14, 0, 10), min_work=True)
        self.assertEqual(self.check(at_seconds(MONDAY, 14, 0, 45)), [])
        self.assertEqual(self.outcomes()[(MONDAY, "min_work")], "baseline")
        self.send.assert_not_called()

    def test_clearing_pause_after_unobserved_crossing_does_not_send(self) -> None:
        self.configure(at(MONDAY, 8), paused_until=MONDAY, min_work=True)
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at_seconds(MONDAY, 13, 59, 45)), [])
        self.configure(at_seconds(MONDAY, 14, 0, 10), min_work=True)
        self.assertEqual(self.check(at_seconds(MONDAY, 14, 0, 45)), [])
        self.assertEqual(self.outcomes()[(MONDAY, "min_work")], "baseline")
        self.send.assert_not_called()

    def test_unrelated_settings_change_keeps_due_milestone(self) -> None:
        self.configure(at(MONDAY, 8), min_work=True)
        self.start(at(MONDAY, 8))
        notifications.update_settings(self.db, False, None, at(MONDAY, 14, 0))
        self.assertEqual(self.check(at(MONDAY, 14, 1)), [(MONDAY, "min_work")])

    def test_pause_mutes_milestones(self) -> None:
        self.configure(
            at(MONDAY, 8),
            paused_until=MONDAY,
            min_work=True,
            daily_target=True,
            daily_max=True,
        )
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at(MONDAY, 19)), [])
        self.assertEqual(
            self.outcomes(),
            {
                (MONDAY, "min_work"): "paused",
                (MONDAY, "daily_target"): "paused",
                (MONDAY, "daily_max"): "paused",
            },
        )
        self.send.assert_not_called()

    def test_disabled_milestones_are_recorded_without_sending(self) -> None:
        self.configure(at(MONDAY, 8), daily_max=True)
        self.start(at(MONDAY, 8))
        self.assertEqual(self.check(at(MONDAY, 17)), [])
        self.assertEqual(self.outcomes()[(MONDAY, "min_work")], "disabled")
        self.assertEqual(self.outcomes()[(MONDAY, "daily_target")], "disabled")

    def test_old_manual_entry_reaching_cap_is_stale(self) -> None:
        self.configure(at(MONDAY, 5), daily_max=True)
        self.completed(at(MONDAY, 6), at(MONDAY, 16, 30))
        self.assertEqual(self.check(at(MONDAY, 18)), [])
        self.assertEqual(self.outcomes()[(MONDAY, "daily_max")], "stale")
        self.send.assert_not_called()

    def test_recent_manual_entry_reaching_cap_sends_generic_message(self) -> None:
        self.configure(at(MONDAY, 5), daily_max=True)
        self.completed(at(MONDAY, 7), at(MONDAY, 17, 30))
        self.assertEqual(self.check(at(MONDAY, 17, 45)), [(MONDAY, "daily_max")])
        _, message = self.send.call_args.args
        self.assertIn("automatisch beendet", message)
        self.assertNotIn("gestoppt", message)
        self.assertNotIn("Arbeitstag", message)


class MilestoneSettingsTest(MilestoneTestCase):
    def test_flags_default_off_and_partial_update_keeps_them(self) -> None:
        loaded = notifications.get_settings(self.db, at(MONDAY, 8))
        self.assertFalse(loaded.notify_min_work)
        self.assertFalse(loaded.notify_daily_target)
        self.assertFalse(loaded.notify_daily_max)

        with patch("app.routers.api.datetime") as clock:
            clock.now.return_value = at(MONDAY, 8)
            api.update_notification_settings(
                NotificationSettingsUpdate(
                    enabled=True, notify_daily_target=True, notify_daily_max=True
                ),
                self.db,
            )
            # A stale page without milestone fields must not reset them.
            updated = api.update_notification_settings(
                NotificationSettingsUpdate(enabled=False), self.db
            )
        self.assertFalse(updated.enabled)
        self.assertFalse(updated.notify_min_work)
        self.assertTrue(updated.notify_daily_target)
        self.assertTrue(updated.notify_daily_max)
        with self.SessionLocal() as observer:
            stored = observer.get(MilestoneSettings, 1)
            self.assertTrue(stored.notify_daily_target)


class LifespanTest(unittest.TestCase):
    def run_lifespan(self, configured: bool) -> AsyncMock:
        loop = AsyncMock()

        async def scenario() -> None:
            async with main.lifespan(main.app):
                await asyncio.sleep(0)

        with (
            patch.object(main, "init_db"),
            patch.object(main, "run_reminder_loop", loop),
            patch.object(main.config, "GOTIFY_CONFIGURED", configured),
        ):
            asyncio.run(scenario())
        return loop

    def test_loop_not_started_without_gotify(self) -> None:
        self.run_lifespan(configured=False).assert_not_called()

    def test_loop_started_with_gotify(self) -> None:
        self.run_lifespan(configured=True).assert_called_once()


if __name__ == "__main__":
    unittest.main()
