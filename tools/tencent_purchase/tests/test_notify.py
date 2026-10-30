from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from tools.tencent_purchase.notify import NotificationError, PushPlusNotifier


class Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class NotifyTests(unittest.TestCase):
    def test_success(self) -> None:
        opener = mock.Mock(return_value=Response({"code": 200}))
        PushPlusNotifier("token", opener=opener).send("title", "body")
        self.assertEqual(opener.call_count, 1)

    def test_retry_and_failure(self) -> None:
        opener = mock.Mock(return_value=Response({"code": 500}))
        notifier = PushPlusNotifier(
            "token",
            attempts=2,
            sleeper=lambda _seconds: None,
            opener=opener,
        )
        with self.assertRaises(NotificationError):
            notifier.send("title", "body")
        self.assertEqual(opener.call_count, 2)

    def test_missing_token_does_not_call_network(self) -> None:
        opener = mock.Mock()
        with self.assertRaises(NotificationError):
            PushPlusNotifier("", opener=opener).send("title", "body")
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
