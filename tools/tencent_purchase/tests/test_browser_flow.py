from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from unittest import mock

from tools.tencent_purchase.browser import StopFlow, TencentPurchaseBrowser
from tools.tencent_purchase.outcome import OutcomeKind
from tools.tencent_purchase.schedule import CHINA_TZ, fixed_session
from tools.tencent_purchase.settings import RunnerSettings


class FakePage:
    def __init__(self) -> None:
        self.reload_count = 0
        self.url = "https://cloud.tencent.com/act/pro/double11-202611"
        self.context = object()
        self.locator_obj = FakeLocator()
        self.evaluate_result: dict[str, object] = {}

    def reload(self, **_kwargs: object) -> None:
        self.reload_count += 1

    def locator(self, _selector: str) -> "FakeLocator":
        return self.locator_obj

    def evaluate(self, _script: str, _arg: object = None) -> dict[str, object]:
        return self.evaluate_result


class FakeLocator:
    def count(self) -> int:
        return 0

    def locator(self, _selector: str) -> "FakeLocator":
        return self


class FakeCookieContext:
    def __init__(self, cookies: list[dict[str, str]]) -> None:
        self.values = cookies

    def cookies(self) -> list[dict[str, str]]:
        return self.values


class FakeResetContext(FakeCookieContext):
    def __init__(self, cookies: list[dict[str, str]]) -> None:
        super().__init__(cookies)
        self.cleared = False
        self.restored: list[dict[str, str]] = []

    def clear_cookies(self) -> None:
        self.cleared = True

    def add_cookies(self, cookies: list[dict[str, str]]) -> None:
        self.restored.extend(cookies)


class FakeActivityRoot:
    def wait_for(self, **_kwargs: object) -> None:
        return None

    def scroll_into_view_if_needed(self) -> None:
        return None


class FakeNavigationPage:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.goto_calls = 0
        self.url = "https://cloud.tencent.com/act/pro/double11-202611"

    def goto(self, _url: str, **_kwargs: object) -> None:
        self.goto_calls += 1
        if self.goto_calls <= self.failures:
            raise RuntimeError("net::ERR_ABORTED")

    def locator(self, _selector: str) -> FakeActivityRoot:
        return FakeActivityRoot()

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class BrowserFlowTests(unittest.TestCase):
    def test_poll_boundary_does_not_reload_preloaded_page(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 27, 9, 59, 55, tzinfo=CHINA_TZ),
            "10:00",
        )
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings, clock=lambda: session.poll_start)
        page = FakePage()
        card = object()
        snapshot = {
            "text": (
                "轻量应用服务器 4核4G3M 300GB 月流量 "
                "活动价：38元 日常价：780元"
            ),
            "cta": "立即抢购",
            "disabled": False,
            "progress": "0%",
        }
        page.evaluate_result = {"state": "active", "snapshot": snapshot}
        with (
            mock.patch.object(browser, "_wait_for_login_state", return_value=True),
        ):
            observed, target_token = browser._wait_for_active_cta(
                page,  # type: ignore[arg-type]
                session,
            )
        self.assertEqual(page.reload_count, 0)
        self.assertEqual(observed["cta"], "立即抢购")
        self.assertTrue(target_token.startswith("tp-active-"))

    def test_missing_target_card_is_retryable_not_day_terminal(self) -> None:
        session = fixed_session(
            dt.datetime(2026, 10, 27, 9, 59, 55, tzinfo=CHINA_TZ),
            "10:00",
        )
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings, sleeper=lambda _seconds: None)
        with (
            mock.patch.object(browser, "_save_evidence"),
            mock.patch.object(browser, "_save_dom_snapshot"),
            self.assertRaises(Exception) as raised,
        ):
            browser._target_card(FakePage(), timeout_seconds=0)  # type: ignore[arg-type]
        self.assertEqual(raised.exception.outcome.kind, OutcomeKind.RETRYABLE_FAILURE)

    def test_pre_submit_stock_warning_is_retryable(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings)
        page = FakePage()
        page.evaluate_result = ["抱歉，当前商品已售罄"]
        blocked = browser._visible_order_blocker(page)  # type: ignore[arg-type]
        self.assertEqual(blocked, (OutcomeKind.RETRYABLE_FAILURE, "商品已售罄"))

    def test_pre_submit_eligibility_warning_halts_day(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings)
        page = FakePage()
        page.evaluate_result = ["对不起，您不满足活动参与条件"]
        blocked = browser._visible_order_blocker(page)  # type: ignore[arg-type]
        self.assertEqual(
            blocked,
            (OutcomeKind.HALTED, "不满足活动参与条件"),
        )

    def test_config_dialog_can_resolve_to_sold_out(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings)
        page = FakePage()
        page.evaluate_result = {"state": "unavailable", "detail": "已抢光"}
        with (
            mock.patch.object(browser, "_save_evidence"),
            mock.patch.object(browser, "_save_dom_snapshot"),
            self.assertRaises(StopFlow) as raised,
        ):
            browser._wait_for_config_dialog(page)  # type: ignore[arg-type]
        self.assertEqual(
            raised.exception.outcome.kind,
            OutcomeKind.RETRYABLE_FAILURE,
        )

    def test_activity_login_requires_cookie_and_api_success(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings)
        page = FakePage()
        page.evaluate_result = {"code": 0}
        self.assertTrue(
            browser._activity_login_ok(
                page,  # type: ignore[arg-type]
                FakeCookieContext([{"name": "skey", "value": "abc"}]),  # type: ignore[arg-type]
            )
        )
        self.assertFalse(
            browser._activity_login_ok(
                page,  # type: ignore[arg-type]
                FakeCookieContext([{"name": "qcloud_uid", "value": "123"}]),  # type: ignore[arg-type]
            )
        )
        page.evaluate_result = {"code": "NOT-LOGINED"}
        self.assertFalse(
            browser._activity_login_ok(
                page,  # type: ignore[arg-type]
                FakeCookieContext([{"name": "p_skey", "value": "abc"}]),  # type: ignore[arg-type]
            )
        )

    def test_reset_login_cookies_removes_only_tencent_domains(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings)
        context = FakeResetContext(
            [
                {
                    "name": "skey",
                    "value": "secret",
                    "domain": ".cloud.tencent.com",
                    "path": "/",
                },
                {
                    "name": "kept",
                    "value": "value",
                    "domain": ".example.com",
                    "path": "/",
                },
            ]
        )
        removed = browser._clear_tencent_cookies(
            context,  # type: ignore[arg-type]
        )
        self.assertEqual(removed, 1)
        self.assertEqual(
            context.restored,
            [
                {
                    "name": "kept",
                    "value": "value",
                    "domain": ".example.com",
                    "path": "/",
                },
            ],
        )
        self.assertTrue(context.cleared)

    def test_activity_login_only_persists_session_tencent_cookies(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings)
        context = FakeResetContext(
            [
                {
                    "name": "skey",
                    "value": "secret",
                    "domain": ".cloud.tencent.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "already_persistent",
                    "value": "value",
                    "domain": ".cloud.tencent.com",
                    "path": "/",
                    "expires": 4_000_000_000,
                },
                {
                    "name": "other",
                    "value": "value",
                    "domain": ".example.com",
                    "path": "/",
                    "expires": -1,
                },
            ]
        )
        count = browser._persist_tencent_login(context)  # type: ignore[arg-type]
        self.assertEqual(count, 1)
        self.assertEqual(len(context.restored), 1)
        stored = context.restored[0]
        self.assertEqual(stored["name"], "skey")
        self.assertGreater(int(stored["expires"]), 1_700_000_000)

    def test_activity_load_retries_one_transient_navigation_error(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings, sleeper=lambda _seconds: None)
        page = FakeNavigationPage(failures=1)
        browser._load_activity(page)  # type: ignore[arg-type]
        self.assertEqual(page.goto_calls, 2)

    def test_activity_load_does_not_retry_more_than_once(self) -> None:
        settings = RunnerSettings(home=Path("/tmp"), pushplus_token="")
        browser = TencentPurchaseBrowser(settings, sleeper=lambda _seconds: None)
        page = FakeNavigationPage(failures=2)
        with self.assertRaisesRegex(RuntimeError, "ERR_ABORTED"):
            browser._load_activity(page)  # type: ignore[arg-type]
        self.assertEqual(page.goto_calls, 2)


if __name__ == "__main__":
    unittest.main()
