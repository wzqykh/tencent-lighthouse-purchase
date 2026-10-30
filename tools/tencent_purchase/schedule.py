"""Sale-session selection and bounded waiting."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Callable
from zoneinfo import ZoneInfo

from .constants import (
    BROWSER_READY_BEFORE,
    POLL_END_AFTER,
    POLL_START_BEFORE,
    TASK_START_SLOP,
    SESSION_STARTS,
    TASK_START_BEFORE,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")


class OutsideSaleWindow(RuntimeError):
    pass


@dataclass(frozen=True)
class SaleSession:
    session_id: str
    start: dt.datetime

    @property
    def poll_start(self) -> dt.datetime:
        return self.start - POLL_START_BEFORE

    @property
    def poll_end(self) -> dt.datetime:
        return self.start + POLL_END_AFTER

    @property
    def browser_ready_at(self) -> dt.datetime:
        return self.start - BROWSER_READY_BEFORE


def now_china() -> dt.datetime:
    return dt.datetime.now(tz=CHINA_TZ)


def aware_china(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_TZ)
    return value.astimezone(CHINA_TZ)


def current_session(value: dt.datetime) -> SaleSession:
    current = aware_china(value)
    for session_id, start_time in SESSION_STARTS.items():
        start = dt.datetime.combine(current.date(), start_time, tzinfo=CHINA_TZ)
        if (
            start - TASK_START_BEFORE - TASK_START_SLOP
            <= current
            <= start + POLL_END_AFTER
        ):
            return SaleSession(session_id=session_id, start=start)
    raise OutsideSaleWindow(
        f"{current.isoformat()} is outside the configured 10:00/15:00 task windows"
    )


def fixed_session(value: dt.datetime, session_id: str) -> SaleSession:
    current = aware_china(value)
    if session_id not in SESSION_STARTS:
        raise ValueError(f"unknown session: {session_id}")
    start = dt.datetime.combine(
        current.date(),
        SESSION_STARTS[session_id],
        tzinfo=CHINA_TZ,
    )
    return SaleSession(session_id=session_id, start=start)


def next_session(
    value: dt.datetime,
    *,
    session_id: str | None = None,
) -> SaleSession:
    current = aware_china(value)
    session_ids = (session_id,) if session_id else tuple(SESSION_STARTS)
    candidates: list[SaleSession] = []
    for offset in (0, 1):
        day = current.date() + dt.timedelta(days=offset)
        for candidate_id in session_ids:
            start = dt.datetime.combine(
                day,
                SESSION_STARTS[candidate_id],
                tzinfo=CHINA_TZ,
            )
            if start > current:
                candidates.append(
                    SaleSession(session_id=candidate_id, start=start)
                )
    if not candidates:
        raise OutsideSaleWindow("no upcoming sale session was found")
    return min(candidates, key=lambda candidate: candidate.start)


def wait_until(
    target: dt.datetime,
    *,
    clock: Callable[[], dt.datetime] = now_china,
    sleeper: Callable[[float], None] = time.sleep,
    on_wait: Callable[[float], None] | None = None,
) -> None:
    while True:
        remaining = (target - clock()).total_seconds()
        if remaining <= 0:
            return
        if on_wait is not None:
            on_wait(remaining)
        sleeper(min(remaining, 30.0))
