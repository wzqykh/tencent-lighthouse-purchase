from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.tencent_purchase.api import csrf_token_from_skey
from tools.tencent_purchase.browser import StopFlow
from tools.tencent_purchase.direct_api import (
    BURST_SUBMIT_SCRIPT,
    TencentDirectApiBrowser,
)
from tools.tencent_purchase.settings import RunnerSettings


class FakeContext:
    def __init__(self, cookies: list[dict[str, str]]) -> None:
        self.cookies_list = cookies

    def cookies(self) -> list[dict[str, str]]:
        return self.cookies_list


class DirectApiTests(unittest.TestCase):
    def browser(self, temporary: str) -> TencentDirectApiBrowser:
        return TencentDirectApiBrowser(
            RunnerSettings(home=Path(temporary), pushplus_token="")
        )

    def test_auth_material_uses_skey_without_returning_cookie_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            browser = self.browser(temporary)
            csrf, signing_skey = browser._auth_material(
                FakeContext(
                    [
                        {"name": "skey", "value": "abc"},
                        {"name": "p_skey", "value": "other"},
                    ]
                )
            )
        self.assertEqual(csrf, csrf_token_from_skey("abc"))
        self.assertEqual(signing_skey, "abc")

    def test_missing_activity_login_halts_before_order_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            browser = self.browser(temporary)
            with self.assertRaises(StopFlow) as raised:
                browser._auth_material(
                    FakeContext([{"name": "qcloud_uid", "value": "123"}])
                )
        self.assertIn("login material", raised.exception.outcome.reason)

    def test_auth_material_accepts_main_csrf_cookie_for_new_login(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            browser = self.browser(temporary)
            csrf, signing_skey = browser._auth_material(
                FakeContext(
                    [
                        {"name": "qcmainCSRFToken", "value": "main-token"},
                        {"name": "qcloud_uid", "value": "123"},
                    ]
                )
            )
        self.assertEqual(csrf, "main-token")
        self.assertEqual(signing_skey, "")

    def test_burst_script_has_bounds_and_state_trace(self) -> None:
        self.assertIn("hardStopServerMs", BURST_SUBMIT_SCRIPT)
        self.assertIn("maxAttempts", BURST_SUBMIT_SCRIPT)
        self.assertIn("setInterval", BURST_SUBMIT_SCRIPT)
        self.assertIn("successSeen", BURST_SUBMIT_SCRIPT)
        self.assertIn("attempts >= Number(maxAttempts)", BURST_SUBMIT_SCRIPT)
        self.assertIn("targetServerMs + 5_000", BURST_SUBMIT_SCRIPT)


if __name__ == "__main__":
    unittest.main()
