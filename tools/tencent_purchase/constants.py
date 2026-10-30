"""Stable product and timing facts for the confirmed Tencent Cloud deal."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

ACTIVITY_URL = (
    "https://cloud.tencent.com/act/pro/double11-202611"
    "?page=featured-202607"
    "&s_source=https%3A%2F%2Fcloud.tencent.com%2Fact%2Fpro%2Ffeatured-202504"
)

PRODUCT_NAME = "轻量应用服务器"
PRODUCT_SKU = "4核4G3M"
PRODUCT_TRAFFIC = "300GB 月流量"
PRODUCT_DISK = "40GB SSD"
PRODUCT_DURATION = "1年"
PRODUCT_PRICE = Decimal("38")
PRODUCT_ORIGINAL_PRICE = Decimal("780")
REQUIRED_TOTAL = Decimal("38.00")

REGION_PRIORITY = ("北京", "上海", "广州")
ALLOWED_REGIONS = frozenset(REGION_PRIORITY)

ACTIVE_CTA = "立即抢购"
PRESALE_CTAS = frozenset({"添加提醒", "取消提醒"})
UNAVAILABLE_CTAS = frozenset({"已抢光", "已结束"})

POLL_START_BEFORE = dt.timedelta(seconds=5)
POLL_END_AFTER = dt.timedelta(seconds=20)
BROWSER_READY_BEFORE = dt.timedelta(minutes=5)
TASK_START_BEFORE = dt.timedelta(minutes=5)
TASK_START_SLOP = dt.timedelta(seconds=30)
SUBMIT_RESULT_TIMEOUT_SECONDS = 10

SESSION_STARTS = {
    "10:00": dt.time(10, 0),
    "15:00": dt.time(15, 0),
}
