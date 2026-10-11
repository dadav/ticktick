import asyncio
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from app.database import Base
from app.models import NotificationSettings, TimerState, WorkSession
from app.routers import api
from app.schemas import NotificationSettingsUpdate
from app.services import gotify, notifications

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
