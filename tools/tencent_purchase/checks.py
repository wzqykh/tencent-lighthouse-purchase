"""Pure validation helpers for page snapshots and order confirmation text."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .constants import (
    ACTIVE_CTA,
    ALLOWED_REGIONS,
    PRESALE_CTAS,
    PRODUCT_DISK,
    PRODUCT_DURATION,
    PRODUCT_NAME,
    PRODUCT_ORIGINAL_PRICE,
    PRODUCT_PRICE,
    PRODUCT_SKU,
    PRODUCT_TRAFFIC,
    REQUIRED_TOTAL,
    REGION_PRIORITY,
    UNAVAILABLE_CTAS,
)


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").replace("：", ":")


def parse_price(value: str) -> Decimal | None:
    match = re.search(r"(?:¥|￥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", value or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def extract_real_total(value: str) -> Decimal | None:
    text = compact(value)
    patterns = (
        r"实付金额:?(?:¥|￥)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)元",
        r"应付金额:?(?:¥|￥)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)元",
        r"订单金额:?(?:¥|￥)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)元",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return parse_price(match.group(1))
    return None


def validate_card_text(value: str) -> list[str]:
    text = compact(value)
    required = tuple(
        compact(item)
        for item in (
            PRODUCT_NAME,
            PRODUCT_SKU,
            PRODUCT_TRAFFIC,
            f"活动价:{PRODUCT_PRICE}元",
            f"日常价:{PRODUCT_ORIGINAL_PRICE:g}元",
        )
    )
    return [f"missing card text: {item}" for item in required if item not in text]


def classify_cta(value: str, disabled: bool) -> str:
    text = compact(value)
    if text == compact(ACTIVE_CTA):
        return "unavailable" if disabled else "active"
    if text in PRESALE_CTAS:
        return "presale"
    if any(item in text for item in UNAVAILABLE_CTAS):
        return "unavailable"
    return "unknown"


def validate_selected_region(value: str) -> bool:
    return compact(value) in ALLOWED_REGIONS


def region_candidates(default: str = "") -> tuple[str, ...]:
    selected = compact(default)
    ordered: list[str] = []
    if selected in ALLOWED_REGIONS:
        ordered.append(selected)
    ordered.extend(
        item for item in REGION_PRIORITY if compact(item) not in ordered
    )
    return tuple(ordered)


def validate_order_text(value: str, *, region: str) -> list[str]:
    text = compact(value)
    required = (
        PRODUCT_NAME,
        PRODUCT_SKU,
        "300GB",
        "40GB",
        "1年",
        "linux",
        region,
    )
    return [f"missing order text: {item}" for item in required if item.lower() not in text.lower()]


def validate_total(total: Decimal | None) -> bool:
    return total is not None and total == REQUIRED_TOTAL


def explicit_submit_failure(value: str) -> bool:
    text = compact(value)
    markers = (
        "订单提交失败",
        "下单失败",
        "生成订单失败",
        "商品已售罄",
        "已抢光",
        "不满足购买条件",
        "已达到单个用户优惠限额",
    )
    return any(marker in text for marker in markers)


def reached_payment_stage(title: str, value: str) -> bool:
    title_text = compact(title)
    body = compact(value)
    return "支付订单-腾讯云" in title_text or "订单提交成功,待支付" in body


def card_snapshot_problems(snapshot: dict[str, object]) -> list[str]:
    text = str(snapshot.get("text", ""))
    problems = validate_card_text(text)
    cta = str(snapshot.get("cta", ""))
    if not cta:
        problems.append("missing purchase CTA")
    if PRODUCT_PRICE != Decimal("38"):
        problems.append("configured product price changed")
    if PRODUCT_ORIGINAL_PRICE != Decimal("780"):
        problems.append("configured original price changed")
    return problems
