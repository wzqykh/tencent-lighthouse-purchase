from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from tools.tencent_purchase.state import DayState, ExecutionLock, StateStore


class StateTests(unittest.TestCase):
    def test_session_is_attempted_once(self) -> None:
        state = DayState(day="2026-10-27")
        self.assertTrue(state.can_start("10:00"))
        state.mark_session("10:00")
        self.assertFalse(state.can_start("10:00"))
        self.assertTrue(state.can_start("15:00"))

    def test_terminal_state_blocks_later_session(self) -> None:
        state = DayState(day="2026-10-27")
        state.mark_session("10:00")
        state.terminate("payment_stage_reached", reason="payment stage")
        self.assertFalse(state.can_start("15:00"))

    def test_state_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = StateStore(Path(temporary))
            state = store.load(dt.date(2026, 10, 27))
            state.mark_session("10:00")
            state.mark_submit_intent()
            state.mark_api_request_started()
            state.mark_api_response_received()
            state.mark_click_returned()
            state.mark_payment_stage()
            state.mark_order_created()
            store.save(state)
            loaded = store.load(dt.date(2026, 10, 27))
            self.assertEqual(loaded.attempted_sessions, ["10:00"])
            self.assertTrue(loaded.submit_attempted)
            self.assertTrue(loaded.submit_intent_at)
            self.assertTrue(loaded.api_request_started_at)
            self.assertTrue(loaded.api_response_at)
            self.assertTrue(loaded.click_returned_at)
            self.assertTrue(loaded.payment_stage_at)
            self.assertTrue(loaded.order_created_at)

    def test_execution_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runner.lock"
            first = ExecutionLock(path)
            second = ExecutionLock(path)
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
