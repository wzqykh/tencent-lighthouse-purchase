"""Outcome types shared by the browser flow and run coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class OutcomeKind(str, Enum):
    ORDER_CREATED = "order_created"
    UNKNOWN = "unknown"
    RETRYABLE_FAILURE = "retryable_failure"
    HALTED = "halted"


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    reason: str
    region: str = ""
    total: Decimal | None = None
    orders: int = 0

    @property
    def terminal_status(self) -> str | None:
        if self.kind is OutcomeKind.ORDER_CREATED:
            return "order_created"
        if self.kind is OutcomeKind.UNKNOWN:
            return "unknown"
        if self.kind is OutcomeKind.HALTED:
            return "halted_today"
        return None
