import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import PausePeriod, WorkSession
from app.services import calculations, statistics, timer


class WorkdayCalculationsTest(unittest.TestCase):
    def session(self, start, end, pauses=()):
        origin = datetime(2026, 9, 15)
        result = WorkSession(
            date=origin.date(),
            start_time=origin + timedelta(hours=start),
            end_time=origin + timedelta(hours=end),
            status="completed",
        )
        result.pause_periods = [
            PausePeriod(
                pause_start=origin + timedelta(hours=a),
                pause_end=origin + timedelta(hours=b),
            )
            for a, b in pauses
        ]
        return result

    def day(self, sessions):
        return calculations.calculate_day(sessions, datetime(2026, 9, 15, 23))

    def test_four_hours_gap_four_hours(self):
        day = self.day([self.session(6, 10), self.session(12, 16)])
        self.assertEqual(day.credited_work_seconds, 8 * 3600)
        self.assertEqual(day.total_pause_seconds, 2 * 3600)
        self.assertEqual(day.lunch_deduction_seconds, 0)
        self.assertEqual(day.remaining_for_daily, "00:12:00")
        self.assertEqual(day.remaining_for_max, "02:00:00")

    def test_short_breaks_accumulate(self):
        for minutes, expected_deduction in [(10, 1200), (20, 600), (30, 0)]:
            with self.subTest(minutes=minutes):
                pauses = [
                    (hour, hour + 10 / 60) for hour in range(8, 8 + minutes // 10)
                ]
                day = self.day([self.session(6, 15, pauses)])
                self.assertEqual(day.lunch_deduction_seconds, expected_deduction)
                self.assertEqual(day.credited_work_seconds, int(8.5 * 3600))

    def test_overlapping_sessions_and_pauses_count_once(self):
        day = self.day([self.session(6, 12, [(8, 10)]), self.session(9, 14)])
        self.assertEqual(day.actual_work_seconds, 7 * 3600)
        self.assertEqual(day.total_pause_seconds, 3600)
        self.assertEqual(day.credited_work_seconds, 7 * 3600)
        self.assertEqual(day.pause_count, 1)

    def test_touching_sessions_do_not_create_a_pause(self):
        day = self.day([self.session(6, 10), self.session(10, 14)])
        self.assertEqual(day.pause_count, 0)
        self.assertEqual(day.credited_work_seconds, int(7.5 * 3600))

    def test_threshold_and_pending_lunch(self):
        day = self.day([self.session(6, 12)])
        self.assertEqual(day.credited_work_seconds, 6 * 3600)
        self.assertEqual(day.lunch_deduction_seconds, 0)
        self.assertEqual(day.remaining_for_daily, "02:42:00")
        after = self.session(6, 12)
        after.end_time += timedelta(seconds=1)
        day = self.day([after])
        self.assertEqual(day.credited_work_seconds, 6 * 3600 - 1799)
        self.assertEqual(day.lunch_deduction_seconds, 1800)

    def test_configuration_with_no_lunch_and_lower_target(self):
        with patch.multiple(
            calculations,
            LUNCH_THRESHOLD_HOURS=24,
            DAILY_REQUIREMENT_MINUTES=240,
            MAX_DAILY_SECONDS=8 * 3600,
        ):
            day = self.day([self.session(6, 11)])
            self.assertEqual(day.credited_work_seconds, 5 * 3600)
            self.assertEqual(day.remaining_for_daily_seconds, 0)
            self.assertEqual(day.remaining_for_max_seconds, 3 * 3600)
        with patch.multiple(
            calculations, LUNCH_THRESHOLD_HOURS=4, LUNCH_DURATION_MINUTES=45
        ):
            day = self.day([self.session(6, 11, [(8, 8.25)])])
            self.assertEqual(day.lunch_deduction_seconds, 1800)

    def test_cap_at_threshold_is_not_missed_after_lunch_drop(self):
        session = self.session(6, 12.1)
        session.end_time = None
        session.status = "active"
        with patch.object(calculations, "MAX_DAILY_SECONDS", 6 * 3600):
            result = calculations.calculate_capped_end_time(
                [session], session, datetime(2026, 9, 15, 12, 5)
            )
        self.assertEqual(result, datetime(2026, 9, 15, 12))

    def test_session_duration_ignores_stale_cache(self):
        session = self.session(6, 16.5)
        session.net_seconds = 10 * 3600
        day = self.day([session])
        self.assertEqual(day.actual_work_seconds, 10 * 3600 + 1800)
        self.assertEqual(day.credited_work_seconds, 10 * 3600)
        self.assertEqual(session.net_seconds, 10 * 3600)

    def test_reset_sessions_do_not_contribute(self):
        discarded = self.session(5, 18)
        discarded.status = "reset"
        day = self.day([self.session(8, 10), discarded])
        self.assertEqual(day.session_count, 1)
        self.assertEqual(day.actual_work_seconds, 7200)
        self.assertEqual(day.total_pause_seconds, 0)


class WorkdayTimerTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.origin = datetime(2026, 9, 15)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def at(self, hour, action, *args):
        now = self.origin + timedelta(hours=hour)
        with patch("app.services.timer.datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            return action(self.db, *args)

    def stats(self, hour=23):
        with patch("app.services.statistics.datetime", wraps=datetime) as clock:
            clock.now.return_value = self.origin + timedelta(hours=hour)
            return statistics.get_statistics(self.db)

    def completed(self, start, end, day_offset=0, net_seconds=None):
        origin = self.origin + timedelta(days=day_offset)
        session = WorkSession(
            date=origin.date(),
            start_time=origin + timedelta(hours=start),
            end_time=origin + timedelta(hours=end),
            status="completed",
            net_seconds=net_seconds,
        )
        self.db.add(session)
        self.db.commit()
        return session

    def test_restart_carries_work_into_both_finish_times(self):
        self.at(6, timer.start_timer)
        self.at(10, timer.stop_timer)
        self.at(16, timer.start_timer)
        status = self.at(20, timer.get_status)
        self.assertEqual(status.day.credited_work_seconds, 8 * 3600)
        self.assertEqual(status.session.net_work_seconds, 4 * 3600)
        self.assertEqual(status.calculations.normal_leave, "20:12")
        self.assertEqual(status.calculations.latest_leave, "22:00")
        self.assertEqual(status.day.pause_count, 1)
        self.assertEqual(status.day.total_pause_seconds, 6 * 3600)

    def test_stopped_total_freezes_until_restart(self):
        self.at(6, timer.start_timer)
        self.at(13, timer.stop_timer)
        stopped = self.at(13, timer.get_status)
        later = self.at(14, timer.get_status)
        self.assertEqual(stopped.day, later.day)
        self.assertEqual(later.day.credited_work_seconds, int(6.5 * 3600))
        self.assertIsNone(later.calculations.normal_leave)
        self.at(14, timer.start_timer)
        restarted = self.at(14, timer.get_status)
        self.assertEqual(restarted.day.credited_work_seconds, 7 * 3600)
        self.assertEqual(restarted.day.session_count, 2)

    def test_pause_restores_lunch_credit_live(self):
        self.at(6, timer.start_timer)
        self.at(13, timer.pause_timer)
        for minute in (0, 10, 20, 30, 40):
            with self.subTest(minute=minute):
                status = self.at(13 + minute / 60, timer.get_status)
                self.assertEqual(status.status, "paused")
                self.assertEqual(status.session.net_work_seconds, 7 * 3600)
                self.assertEqual(
                    status.day.lunch_deduction_seconds, max(0, 30 - minute) * 60
                )
        self.at(14, timer.continue_timer)
        self.assertEqual(
            self.at(14.5, timer.get_status).day.credited_work_seconds, int(7.5 * 3600)
        )

    def test_uninterrupted_cap_is_six_to_sixteen_thirty(self):
        self.at(6, timer.start_timer)
        before = self.at(16, timer.get_status)
        self.assertEqual(before.status, "running")
        self.assertEqual(before.calculations.latest_leave, "16:30")
        self.assertEqual(before.day.remaining_for_max_seconds, 1800)
        capped = self.at(18, timer.get_status)
        self.assertTrue(capped.auto_stopped)
        self.assertEqual(capped.day.credited_work_seconds, 10 * 3600)
        self.assertEqual(
            self.db.query(WorkSession).one().end_time,
            self.origin + timedelta(hours=16.5),
        )
        self.assertFalse(capped.can_start)
        self.assertFalse(self.at(19, timer.start_timer).success)
        self.assertEqual(self.db.query(WorkSession).count(), 1)

    def test_cap_across_three_sessions(self):
        for start, end in [(6, 10), (12, 16)]:
            self.at(start, timer.start_timer)
            self.at(end, timer.stop_timer)
        self.at(18, timer.start_timer)
        status = self.at(22, timer.get_status)
        self.assertTrue(status.auto_stopped)
        latest = self.db.query(WorkSession).order_by(WorkSession.id.desc()).first()
        self.assertEqual(latest.end_time, self.origin + timedelta(hours=20))
        self.assertEqual(latest.net_seconds, 2 * 3600)
        self.assertEqual(status.day.credited_work_seconds, 10 * 3600)

    def test_start_checks_gap_credit_without_saving_gap(self):
        self.at(6, timer.start_timer)
        self.at(16 + 20 / 60, timer.stop_timer)
        status = self.at(17, timer.get_status)
        self.assertEqual(status.day.credited_work_seconds, 9 * 3600 + 50 * 60)
        self.assertFalse(status.can_start)
        self.assertIn("Pausengutschrift", status.start_blocked_reason)
        self.assertFalse(self.at(17, timer.start_timer).success)
        self.assertEqual(self.db.query(WorkSession).count(), 1)
        self.assertEqual(self.db.query(PausePeriod).count(), 0)

    def test_cap_can_be_reached_during_pause(self):
        self.at(6, timer.start_timer)
        self.at(16.25, timer.pause_timer)
        status = self.at(17, timer.get_status)
        self.assertTrue(status.auto_stopped)
        session = self.db.query(WorkSession).one()
        self.assertEqual(session.end_time, self.origin + timedelta(hours=16.5))
        self.assertEqual(session.net_seconds, int(10.25 * 3600))
        self.assertEqual(session.pause_periods[0].pause_end, session.end_time)
        self.assertEqual(status.day.credited_work_seconds, 10 * 3600)

    def test_actions_enforce_cap_without_polling(self):
        for offset, action in enumerate(
            [timer.pause_timer, timer.continue_timer, timer.stop_timer]
        ):
            with self.subTest(action=action.__name__):
                self.origin = datetime(2026, 9, 15) + timedelta(days=offset)
                self.at(6, timer.start_timer)
                if action == timer.continue_timer:
                    self.at(16.25, timer.pause_timer)
                result = self.at(19, action)
                self.assertTrue(result.success)
                self.assertEqual(result.status, "idle")
                session = (
                    self.db.query(WorkSession).order_by(WorkSession.id.desc()).first()
                )
                self.assertEqual(session.end_time, self.origin + timedelta(hours=16.5))

    def test_late_pause_records_are_clipped_after_backdating(self):
        self.at(6, timer.start_timer)
        session = timer.get_active_session(self.db)
        session.pause_periods.append(
            PausePeriod(pause_start=self.origin + timedelta(hours=18))
        )
        self.db.commit()
        self.at(19, timer.get_status)
        self.assertEqual(session.pause_periods[0].pause_start, session.end_time)
        self.assertEqual(session.pause_periods[0].pause_end, session.end_time)

    def test_manual_over_cap_is_preserved_and_edit_can_restore_capacity(self):
        result = self.at(
            20, timer.create_manual_session, "2026-09-15", "06:00", "18:00"
        )
        self.assertTrue(result.success)
        session = self.db.query(WorkSession).one()
        self.assertEqual(session.net_seconds, 12 * 3600)
        status = self.at(20, timer.get_status)
        self.assertTrue(status.day.cap_exceeded)
        self.assertFalse(status.can_start)
        self.assertEqual(self.stats().this_week.total_seconds, int(11.5 * 3600))
        self.at(20, timer.update_session, session.id, None, "14:00")
        self.assertTrue(self.at(20, timer.get_status).can_start)
        self.assertTrue(self.at(20, timer.start_timer).success)

    def test_discard_and_delete_recalculate_retained_sessions(self):
        saved = self.completed(6, 10)
        self.at(16, timer.start_timer)
        self.at(18, timer.reset_timer)
        status = self.at(19, timer.get_status)
        self.assertEqual(status.day.actual_work_seconds, 4 * 3600)
        self.assertEqual(status.day.total_pause_seconds, 0)
        self.at(19, timer.delete_session, saved.id)
        status = self.at(19, timer.get_status)
        self.assertIsNone(status.day)
        self.assertTrue(status.can_start)

    def test_overnight_session_stays_on_start_day(self):
        self.at(23, timer.start_timer)
        status = self.at(25, timer.get_status)
        self.assertEqual(status.day.date, "2026-09-15")
        self.assertEqual(status.day.actual_work_seconds, 7200)
        self.at(25, timer.stop_timer)
        self.assertIsNone(self.at(25, timer.get_status).day)
        self.at(26, timer.start_timer)
        self.assertEqual(self.at(26, timer.get_status).day.date, "2026-09-16")
        history = self.stats(27)
        self.assertEqual(history.recent_days[0].day.date, "2026-09-15")

    def test_ten_recent_days_include_all_their_sessions(self):
        for day in range(11):
            self.completed(6, 7, day_offset=-day)
        for index in range(11):
            self.completed(8 + index / 3, 8 + index / 3 + 1 / 6)
        result = self.stats()
        self.assertEqual(len(result.recent_days), 10)
        self.assertEqual(len(result.recent_days[0].sessions), 12)
        self.assertEqual(len(result.recent_sessions), 10)
        self.assertEqual(result.recent_days[0].day.actual_work_seconds, 3600 + 11 * 600)

    def test_statistics_and_details_recompute_history_from_timestamps(self):
        first = self.completed(6, 10, net_seconds=1)
        second = self.completed(12, 16, net_seconds=2)
        result = self.stats()
        self.assertEqual(result.this_week.total_seconds, 8 * 3600)
        self.assertEqual(result.this_month.total_seconds, 8 * 3600)
        self.assertEqual(result.this_week.average_start_time, "06:00")
        self.assertEqual(result.this_week.average_end_time, "16:00")
        self.assertEqual(result.recent_days[0].day.overtime_formatted, "-00:12:00")
        for session in (first, second):
            details = statistics.get_session_details(self.db, session.id)
            self.assertEqual(details.net_work_formatted, "04:00")
            self.assertEqual(details.overtime_formatted, "-00:12")
        self.assertEqual(first.net_seconds, 1)
        self.assertEqual(second.net_seconds, 2)

    def test_overlapping_manual_sessions_do_not_double_count(self):
        self.completed(6, 12)
        self.completed(10, 15)
        status = self.at(18, timer.get_status)
        self.assertEqual(status.day.actual_work_seconds, 9 * 3600)
        self.assertEqual(status.day.credited_work_seconds, int(8.5 * 3600))
        self.assertTrue(status.can_start)
        self.assertEqual(
            self.stats().this_week.total_seconds, status.day.credited_work_seconds
        )
