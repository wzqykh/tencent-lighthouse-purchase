from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from tools.tencent_purchase.outcome import Outcome, OutcomeKind
from tools.tencent_purchase.runner import PurchaseRunner, notification_for
from tools.tencent_purchase.schedule import CHINA_TZ, fixed_session
from tools.tencent_purchase.settings import RunnerSettings


class RunnerTests(unittest.TestCase):
    def test_created_order_notification_requires_manual_payment(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 27, 9, 55, tzinfo=CHINA_TZ),
            "10:00",
        )
        title, body = notification_for(
            session,
            Outcome(
                OutcomeKind.ORDER_CREATED,
                "created",
                region="北京",
                total=Decimal("38.00"),
            ),
        )
        self.assertIn("已创建未支付订单", title)
        self.assertIn("不会付款", body)
        self.assertIn("手机", body)
        self.assertIn("关闭自动续费", body)

    def test_multiple_created_orders_are_reported_for_cleanup(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 28, 14, 55, tzinfo=CHINA_TZ),
            "15:00",
        )
        title, body = notification_for(
            session,
            Outcome(
                OutcomeKind.ORDER_CREATED,
                "multiple",
                region="上海",
                total=Decimal("38.00"),
                orders=3,
            ),
        )
        self.assertIn("多笔", title)
        self.assertIn("3 个订单标识", body)
        self.assertIn("取消多余订单", body)

    def test_afternoon_retryable_failure_does_not_promise_another_session(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 27, 14, 55, tzinfo=CHINA_TZ),
            "15:00",
        )
        _, body = notification_for(
            session,
            Outcome(OutcomeKind.RETRYABLE_FAILURE, "sold out"),
        )
        self.assertIn("当天已无剩余场次", body)
        self.assertNotIn("15:00 任务会再尝试", body)

    def test_unknown_result_is_terminal_and_blocks_retry_message(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 27, 9, 55, tzinfo=CHINA_TZ),
            "10:00",
        )
        outcome = Outcome(OutcomeKind.UNKNOWN, "timeout")
        self.assertEqual(outcome.terminal_status, "unknown")
        title, body = notification_for(session, outcome)
        self.assertIn("请勿重复下单", title)
        self.assertIn("不会刷新或重试", body)

    def test_order_creation_blocks_afternoon_session(self) -> None:
        morning = fixed_session(
            dt.datetime(2026, 10, 27, 9, 55, tzinfo=CHINA_TZ),
            "10:00",
        )
        afternoon = fixed_session(
            dt.datetime(2026, 10, 27, 14, 55, tzinfo=CHINA_TZ),
            "15:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            settings = RunnerSettings(
                home=Path(temporary),
                pushplus_token="test-token",
            )
            outcome = Outcome(
                OutcomeKind.ORDER_CREATED,
                "created",
                region="北京",
                total=Decimal("38.00"),
            )
            with (
                mock.patch(
                    "tools.tencent_purchase.runner.run_api_browser",
                    return_value=outcome,
                ),
                mock.patch(
                    "tools.tencent_purchase.runner.PushPlusNotifier.send"
                ) as send,
            ):
                runner = PurchaseRunner(settings)
                self.assertEqual(runner.run(morning), outcome)
                self.assertIsNone(runner.run(afternoon))
                send.assert_called_once()
            state = runner.state_store.load(morning.start.date())
            self.assertEqual(state.terminal_status, "order_created")
            self.assertEqual(state.attempted_sessions, ["10:00"])


if __name__ == "__main__":
    unittest.main()
