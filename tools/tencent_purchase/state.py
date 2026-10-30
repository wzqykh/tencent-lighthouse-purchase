"""Persistent daily state and a process-scoped execution lock."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

TERMINAL_STATUSES = frozenset(
    {
        "payment_stage_reached",
        "order_created",
        "unknown",
        "halted_today",
    }
)


@dataclass
class DayState:
    day: str
    attempted_sessions: list[str] = field(default_factory=list)
    submit_attempted: bool = False
    submit_intent_at: str = ""
    api_request_started_at: str = ""
    api_response_at: str = ""
    click_returned_at: str = ""
    payment_stage_at: str = ""
    order_created_at: str = ""
    terminal_status: str | None = None
    reason: str = ""
    selected_region: str = ""
    total: str = ""
    updated_at: str = ""

    def can_start(self, session_id: str) -> bool:
        return (
            self.terminal_status not in TERMINAL_STATUSES
            and session_id not in self.attempted_sessions
        )

    def mark_session(self, session_id: str, now: dt.datetime | None = None) -> None:
        if session_id not in self.attempted_sessions:
            self.attempted_sessions.append(session_id)
        self.updated_at = (now or dt.datetime.now(tz=dt.UTC)).isoformat()

    def mark_submit_intent(self, now: dt.datetime | None = None) -> None:
        self.submit_attempted = True
        timestamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat()
        self.submit_intent_at = timestamp
        self.updated_at = timestamp

    def mark_click_returned(self, now: dt.datetime | None = None) -> None:
        timestamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat()
        self.click_returned_at = timestamp
        self.updated_at = timestamp

    def mark_api_request_started(self, now: dt.datetime | None = None) -> None:
        timestamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat()
        self.api_request_started_at = timestamp
        self.updated_at = timestamp

    def mark_api_response_received(self, now: dt.datetime | None = None) -> None:
        timestamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat()
        self.api_response_at = timestamp
        self.updated_at = timestamp

    def mark_payment_stage(self, now: dt.datetime | None = None) -> None:
        timestamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat()
        self.payment_stage_at = timestamp
        self.updated_at = timestamp

    def mark_order_created(self, now: dt.datetime | None = None) -> None:
        timestamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat()
        self.order_created_at = timestamp
        self.updated_at = timestamp

    def terminate(
        self,
        status: str,
        *,
        reason: str = "",
        region: str = "",
        total: str = "",
        now: dt.datetime | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"not a terminal status: {status}")
        self.terminal_status = status
        self.reason = reason
        self.selected_region = region
        self.total = total
        self.updated_at = (now or dt.datetime.now(tz=dt.UTC)).isoformat()


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def path_for(self, value: dt.date) -> Path:
        return self.state_dir / f"{value.isoformat()}.json"

    def load(self, value: dt.date) -> DayState:
        path = self.path_for(value)
        if not path.exists():
            return DayState(day=value.isoformat())
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("day") != value.isoformat():
            raise ValueError(f"invalid state file: {path}")
        sessions = data.get("attempted_sessions", [])
        if not isinstance(sessions, list) or not all(
            isinstance(item, str) for item in sessions
        ):
            raise ValueError(f"invalid attempted_sessions in {path}")
        return DayState(
            day=value.isoformat(),
            attempted_sessions=list(dict.fromkeys(sessions)),
            submit_attempted=bool(data.get("submit_attempted", False)),
            submit_intent_at=str(data.get("submit_intent_at", "")),
            api_request_started_at=str(data.get("api_request_started_at", "")),
            api_response_at=str(data.get("api_response_at", "")),
            click_returned_at=str(data.get("click_returned_at", "")),
            payment_stage_at=str(data.get("payment_stage_at", "")),
            order_created_at=str(data.get("order_created_at", "")),
            terminal_status=data.get("terminal_status"),
            reason=str(data.get("reason", "")),
            selected_region=str(data.get("selected_region", "")),
            total=str(data.get("total", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    def save(self, state: DayState) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(dt.date.fromisoformat(state.day))
        payload = {
            "day": state.day,
            "attempted_sessions": state.attempted_sessions,
            "submit_attempted": state.submit_attempted,
            "submit_intent_at": state.submit_intent_at,
            "api_request_started_at": state.api_request_started_at,
            "api_response_at": state.api_response_at,
            "click_returned_at": state.click_returned_at,
            "payment_stage_at": state.payment_stage_at,
            "order_created_at": state.order_created_at,
            "terminal_status": state.terminal_status,
            "reason": state.reason,
            "selected_region": state.selected_region,
            "total": state.total,
            "updated_at": state.updated_at,
        }
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=self.state_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class ExecutionLock:
    """Non-blocking OS advisory lock that is released when the process exits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("ascii"))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "ExecutionLock":
        if not self.acquire():
            raise RuntimeError("another purchase execution is already running")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
