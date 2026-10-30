"""Pure helpers for the guarded Tencent activity API submission."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable

API_HOST = "https://act-api.cloud.tencent.com"
CHECK_LOGIN_PATH = "/dianshi/check-login"
SCHEDULE_PATH = "/dianshi/query-seckill-by-date"
DO_GOODS_PATH = "/dianshi/do-goods"
ORDER_CHECK_URL = "https://buy.cloud.tencent.com/order/check"

DEFAULT_ACTIVITY_ID = 166111704417621
SHANGHAI_REGION_ID = 4
SHANGHAI_REGION_NAME = "上海"
TARGET_PRODUCT_TYPE = "lighthouse_v5"
TARGET_GOODS_TYPE = "bundle_budget_mc_lg4_01"
TARGET_IMAGE_ID = "lhbp-eqora508"
TARGET_REAL_COST_CENTS = 3800
TARGET_TOTAL_COST_CENTS = 78000


class ApiDisposition(str, Enum):
    ORDER_CREATED = "order_created"
    NOT_STARTED = "not_started"
    SOLD_OUT = "sold_out"
    NOT_LOGGED_IN = "not_logged_in"
    CSRF_ERROR = "csrf_error"
    INELIGIBLE = "ineligible"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ApiOrderResult:
    disposition: ApiDisposition
    code: str
    message: str
    deal_id: str = ""
    deal_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetOffer:
    activity_id: int
    session_id: int
    goods_id: int
    product_type: str
    goods_param: dict[str, object]
    total: Decimal
    server_now_ms: int


def csrf_token_from_skey(value: str) -> str:
    token = 5381
    for character in value:
        token += (token << 5) + ord(character)
    return str(token & 0x7FFFFFFF)


def activity_id_from_requests(requests: Iterable[dict[str, str]]) -> int | None:
    values: dict[int, int] = {}
    for request in requests:
        try:
            payload = json.loads(request.get("post_data", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        value = payload.get("activity_id") if isinstance(payload, dict) else None
        if isinstance(value, int) and value > 0:
            values[value] = values.get(value, 0) + 1
    if not values:
        return None
    return max(values, key=values.get)


def _walk(value: object) -> Iterable[tuple[str, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _session_matches(session: dict[str, object], expected: dt.datetime) -> bool:
    date_value = str(session.get("start_date", ""))
    time_value = str(session.get("start_time", ""))
    try:
        start = dt.datetime.combine(
            dt.date.fromisoformat(date_value),
            dt.time.fromisoformat(time_value),
            tzinfo=expected.tzinfo,
        )
    except ValueError:
        return False
    return start == expected


def _target_sale(session: dict[str, object]) -> dict[str, object] | None:
    sales = session.get("sales")
    if not isinstance(sales, list):
        return None
    matches = [
        sale
        for sale in sales
        if isinstance(sale, dict)
        and sale.get("product_name") == TARGET_PRODUCT_TYPE
        and sale.get("goodsId") == sale.get("act_id")
    ]
    return matches[0] if len(matches) == 1 else None


def target_offer_from_schedule(
    payload: dict[str, object],
    *,
    session_start: dt.datetime,
) -> TargetOffer:
    if payload.get("code") != 0:
        raise ValueError(
            f"schedule query failed: {payload.get('msg') or payload.get('code')}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("schedule response has no data")
    flash_result = data.get("flashResult")
    if not isinstance(flash_result, dict):
        raise ValueError("schedule response has no flashResult")

    matching: list[tuple[dict[str, object], dict[str, object]]] = []
    for sessions in flash_result.values():
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if not _session_matches(session, session_start):
                continue
            sale = _target_sale(session)
            if sale is not None:
                matching.append((session, sale))
    if len(matching) != 1:
        raise ValueError(
            f"expected one target sale for {session_start.isoformat()}, found {len(matching)}"
        )

    session, sale = matching[0]
    offer = sale.get("$goods")
    goods_param = sale.get("defaultOrderParam")
    price_data = sale.get("priceData")
    if not isinstance(offer, dict):
        raise ValueError("target sale has no $goods")
    if not isinstance(goods_param, dict):
        raise ValueError("target sale has no defaultOrderParam")
    if not isinstance(price_data, dict):
        raise ValueError("target sale has no priceData")

    activity_id = int(offer.get("activity_id") or sale.get("activityId") or 0)
    session_id = int(session.get("id") or 0)
    goods_id = int(offer.get("act_id") or sale.get("goodsId") or 0)
    if not activity_id or not session_id or not goods_id:
        raise ValueError("target identifiers are incomplete")
    if int(sale.get("activityId") or 0) != activity_id:
        raise ValueError("target sale activity id mismatch")
    if offer.get("product_name") not in (None, TARGET_PRODUCT_TYPE):
        raise ValueError("target product type mismatch")
    if offer.get("user_num_limit") not in (None, 1):
        raise ValueError("target purchase limit changed")

    required_param = {
        "BlueprintId": "LINUX_UNIX",
        "area": 1,
        "ddocUnionConnect": 0,
        "goodsNum": 1,
        "imageId": TARGET_IMAGE_ID,
        "scenario": "0",
        "timeSpanUnit": "12m",
        "zone": "",
        "regionId": SHANGHAI_REGION_ID,
        "type": TARGET_GOODS_TYPE,
    }
    for key, expected in required_param.items():
        if goods_param.get(key) != expected:
            raise ValueError(
                f"target goods_param {key} mismatch: "
                f"{goods_param.get(key)!r} != {expected!r}"
            )

    real_cost = price_data.get("real_cost")
    total_cost = price_data.get("total_cost")
    if real_cost != TARGET_REAL_COST_CENTS:
        raise ValueError(f"target real_cost changed: {real_cost!r}")
    if total_cost != TARGET_TOTAL_COST_CENTS:
        raise ValueError(f"target total_cost changed: {total_cost!r}")

    server_now = data.get("nowTime")
    if not isinstance(server_now, int):
        raise ValueError("schedule response has no integer server nowTime")

    return TargetOffer(
        activity_id=activity_id,
        session_id=session_id,
        goods_id=goods_id,
        product_type=TARGET_PRODUCT_TYPE,
        goods_param=dict(goods_param),
        total=Decimal("38.00"),
        server_now_ms=server_now,
    )


def build_do_goods_payload(
    offer: TargetOffer,
    *,
    page_url: str,
) -> dict[str, object]:
    return {
        "activity_id": offer.activity_id,
        "preview": 0,
        "agent_channel": {
            "fromChannel": "",
            "fromSales": "",
            "isAgentClient": False,
            "fromUrl": page_url,
            "agent_key": "",
        },
        "business": {
            "id": offer.session_id,
            "from": "lightningDeals",
        },
        "goods": [
            {
                "act_id": offer.goods_id,
                "type": offer.product_type,
                "goods_param": offer.goods_param,
            }
        ],
    }


def _first_value(payload: object, keys: frozenset[str]) -> object:
    for key, value in _walk(payload):
        if key in keys and value not in (None, "", []):
            return value
    return None


def classify_do_goods_response(payload: object) -> ApiOrderResult:
    if not isinstance(payload, dict):
        return ApiOrderResult(
            ApiDisposition.UNKNOWN,
            "",
            "response was not a JSON object",
        )
    code_value = payload.get("code")
    code = "" if code_value is None else str(code_value)
    message = str(payload.get("msg") or payload.get("message") or "")
    combined = f"{code} {message}".lower()

    if code.upper() in {"NOT-LOGINED", "NOT-LOGGED-IN"} or "登陆态校验失败" in message:
        return ApiOrderResult(ApiDisposition.NOT_LOGGED_IN, code, message)
    if code.upper() == "CSRF-ERROR" or "csrf" in combined or "校验失败" in message:
        return ApiOrderResult(ApiDisposition.CSRF_ERROR, code, message)
    if code == "1101001" or any(
        marker in message
        for marker in (
            "尚未开始",
            "还没开始",
            "未开始",
        )
    ):
        return ApiOrderResult(ApiDisposition.NOT_STARTED, code, message)
    if any(
        marker in message
        for marker in (
            "已售罄",
            "已抢光",
            "库存不足",
            "无库存",
        )
    ):
        return ApiOrderResult(ApiDisposition.SOLD_OUT, code, message)
    if any(
        marker in message
        for marker in (
            "不满足活动参与条件",
            "不满足购买条件",
            "已达到单个用户优惠限额",
            "账号风险",
            "实名",
            "face",
            "captcha",
            "验证码",
        )
    ):
        return ApiOrderResult(ApiDisposition.INELIGIBLE, code, message)
    if code == "408" or "系统访问频率过快" in message:
        return ApiOrderResult(ApiDisposition.RATE_LIMITED, code, message)

    if code_value in (0, "0"):
        deal_value = _first_value(
            payload,
            frozenset({"big_deal_no", "deal_id", "dealId"}),
        )
        deal_id = ""
        if isinstance(deal_value, list):
            deal_id = str(deal_value[0]) if deal_value else ""
        elif deal_value not in (None, ""):
            deal_id = str(deal_value)
        name_value = _first_value(payload, frozenset({"deal_names", "dealNames"}))
        deal_names = (
            tuple(str(value) for value in name_value)
            if isinstance(name_value, list)
            else ()
        )
        if deal_id and deal_id != "0":
            return ApiOrderResult(
                ApiDisposition.ORDER_CREATED,
                code,
                message,
                deal_id=deal_id,
                deal_names=deal_names,
            )
        return ApiOrderResult(ApiDisposition.UNKNOWN, code, message)

    return ApiOrderResult(ApiDisposition.UNKNOWN, code, message)
