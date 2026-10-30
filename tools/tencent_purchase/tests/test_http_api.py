from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.tencent_purchase.http_api import (
    ClockSample,
    DIRECT_CLOCK_SAMPLE_BEFORE,
    DIRECT_CLOCK_SAMPLES,
    DIRECT_CONNECTION_WARMUP_BEFORE,
    DIRECT_408_BACKOFF_SECONDS,
    DIRECT_BATCH_GAP_MAX_SECONDS,
    DIRECT_BATCH_GAP_MIN_SECONDS,
    DIRECT_BATCH_SIZE,
    DIRECT_START_BEFORE,
    DIRECT_STOP_AFTER,
    TencentHttpApiBrowser,
    best_clock_sample,
    cookie_header,
    server_now_from_payload,
)
from tools.tencent_purchase.settings import RunnerSettings


class HttpApiTests(unittest.TestCase):
    def test_cookie_header_is_compact_and_deduplicated(self) -> None:
        value = cookie_header(
            [
                {"name": "a", "value": "1"},
                {"name": "a", "value": "2"},
                {"name": "b", "value": "3"},
                {"name": "", "value": "ignored"},
            ]
        )
        self.assertEqual(value, "a=1; b=3")

    def test_server_now_accepts_number_or_object(self) -> None:
        self.assertEqual(
            server_now_from_payload({"code": 0, "data": 1793325390755}),
            1793325390755,
        )
        self.assertEqual(
            server_now_from_payload(
                {"code": 0, "data": {"nowTime": 1793325390755}}
            ),
            1793325390755,
        )
        self.assertIsNone(server_now_from_payload({"code": 408, "data": 1}))

    def test_lowest_rtt_clock_sample_wins(self) -> None:
        samples = [
            ClockSample(1_000, 80.0, 10.0),
            ClockSample(1_020, 20.0, 11.0),
            ClockSample(1_040, 45.0, 12.0),
        ]
        selected = best_clock_sample(samples)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.rtt_ms, 20.0)
        self.assertEqual(selected.corrected_server_ms, 1_030)
        self.assertIsNone(best_clock_sample([]))

    def test_http_runner_has_bounded_direct_request_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            browser = TencentHttpApiBrowser(
                RunnerSettings(home=Path(temporary), pushplus_token="")
            )
        self.assertEqual(browser.__class__.__name__, "TencentHttpApiBrowser")
        self.assertEqual(DIRECT_CONNECTION_WARMUP_BEFORE.total_seconds(), 20)
        self.assertEqual(DIRECT_CLOCK_SAMPLE_BEFORE.total_seconds(), 10)
        self.assertEqual(DIRECT_CLOCK_SAMPLES, 2)
        self.assertEqual(DIRECT_START_BEFORE.total_seconds(), 1.5)
        self.assertEqual(DIRECT_STOP_AFTER.total_seconds(), 30)
        self.assertEqual(DIRECT_BATCH_SIZE, 10)
        self.assertEqual(DIRECT_BATCH_GAP_MIN_SECONDS, 0.35)
        self.assertEqual(DIRECT_BATCH_GAP_MAX_SECONDS, 0.60)
        self.assertEqual(DIRECT_408_BACKOFF_SECONDS, 1.5)


if __name__ == "__main__":
    unittest.main()
