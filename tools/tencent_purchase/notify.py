"""PushPlus notifications with bounded retries and injectable transport."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable

PUSHPLUS_URL = "https://www.pushplus.plus/send"


class NotificationError(RuntimeError):
    pass


class PushPlusNotifier:
    def __init__(
        self,
        token: str,
        *,
        timeout: int = 15,
        attempts: int = 6,
        sleeper: Callable[[float], None] = time.sleep,
        opener: Callable[..., object] = urllib.request.urlopen,
    ) -> None:
        self.token = token.strip()
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.sleeper = sleeper
        self.opener = opener

    def send(self, title: str, body: str) -> None:
        if not self.token:
            raise NotificationError("PURCHASE_PUSHPLUS_TOKEN is not configured")
        payload = json.dumps(
            {
                "token": self.token,
                "title": title[:100],
                "content": body[:20000],
                "template": "txt",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            request = urllib.request.Request(
                PUSHPLUS_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    result = json.loads(
                        response.read().decode("utf-8", errors="replace") or "{}"
                    )
                if int(result.get("code") or 0) == 200:
                    return
                last_error = NotificationError(
                    f"PushPlus returned code={result.get('code')}"
                )
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                last_error = error
            if attempt + 1 < self.attempts:
                self.sleeper(min(2**attempt, 15))
        raise NotificationError("PushPlus delivery failed") from last_error
