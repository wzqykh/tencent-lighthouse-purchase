from __future__ import annotations

import datetime as dt
import unittest

from tools.tencent_purchase.schedule import (
    CHINA_TZ,
    OutsideSaleWindow,
    current_session,
    fixed_session,
    next_session,
)


class ScheduleTests(unittest.TestCase):
    def test_ten_oclock_task_window(self) -> None:
        session = current_session(dt.datetime(2026, 10, 27, 9, 55, tzinfo=CHINA_TZ))
        self.assertEqual(session.session_id, "10:00")
        self.assertEqual(session.start.hour, 10)

    def test_scheduler_clock_slop_is_accepted(self) -> None:
        session = current_session(dt.datetime(2026, 10, 27, 9, 54, 40, tzinfo=CHINA_TZ))
        self.assertEqual(session.session_id, "10:00")

    def test_fifteen_oclock_task_window(self) -> None:
        session = current_session(dt.datetime(2026, 10, 27, 14, 55, tzinfo=CHINA_TZ))
        self.assertEqual(session.session_id, "15:00")

    def test_outside_window_fails_closed(self) -> None:
        with self.assertRaises(OutsideSaleWindow):
            current_session(dt.datetime(2026, 10, 27, 11, 0, tzinfo=CHINA_TZ))

    def test_fixed_session_uses_local_date(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 27, 9, 0, tzinfo=CHINA_TZ),
            "15:00",
        )
        self.assertEqual(session.start, dt.datetime(2026, 10, 27, 15, tzinfo=CHINA_TZ))

    def test_next_session_crosses_midnight_for_preflight(self) -> None:
        session = next_session(
            dt.datetime(2026, 10, 27, 18, 0, tzinfo=CHINA_TZ),
            session_id="10:00",
        )
        self.assertEqual(session.session_id, "10:00")
        self.assertEqual(
            session.start,
            dt.datetime(2026, 10, 28, 10, 0, tzinfo=CHINA_TZ),
        )


if __name__ == "__main__":
    unittest.main()
