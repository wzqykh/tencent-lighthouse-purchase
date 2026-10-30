"""Monitor current-session inventory and attempt to catch returned stock."""

from __future__ import annotations

import datetime as dt
import logging
import random
import time
from decimal import Decimal
from typing import Any

from .api import (
    API_HOST,
    DEFAULT_ACTIVITY_ID,
    DO_GOODS_PATH,
    SCHEDULE_PATH,
    SHANGHAI_REGION_NAME,
    build_do_goods_payload,
    target_offer_from_schedule,
)
from .browser import StopFlow
from .direct_api import TencentDirectApiBrowser
from .outcome import Outcome, OutcomeKind
from .schedule import SaleSession, now_china

LOGGER = logging.getLogger(__name__)

CHECK_AVAILABLE_PATH = "/dianshi/check-available"
RESTOCK_POLL_MIN_SECONDS = 1.5
RESTOCK_POLL_MAX_SECONDS = 2.5
RESTOCK_BURST_ATTEMPTS = 3
RESTOCK_BURST_INTERVAL_MS = 100
RESTOCK_408_COOLDOWN_MIN_SECONDS = 11.0
RESTOCK_408_COOLDOWN_MAX_SECONDS = 13.0
RESTOCK_NETWORK_RETRY_MIN_SECONDS = 2.0
RESTOCK_NETWORK_RETRY_MAX_SECONDS = 5.0
RESTOCK_MAX_CONSECUTIVE_NETWORK_FAILURES = 5
RESTOCK_END_BEFORE_END = dt.timedelta(minutes=5)


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def has_shanghai_stock(
    payload: dict[str, object],
    *,
    goods_type: str,
) -> bool:
    if payload.get("code") not in (0, "0"):
        return False
    data = payload.get("data")
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        regional_checked = False
        quota = item.get("quota")
        if isinstance(quota, dict):
            region = quota.get("4")
            if isinstance(region, dict):
                target = region.get(goods_type)
                if isinstance(target, dict):
                    regional_checked = True
                    if (
                        _as_int(target.get("available"))
                        - _as_int(target.get("reserved"))
                        > 0
                    ):
                        return True
        if not regional_checked and (
            _as_int(item.get("available")) > 0
            and _as_int(item.get("cycle_available")) > 0
        ):
            return True
    return False


class TencentRestockBrowser(TencentDirectApiBrowser):
    def watch(self, session: SaleSession) -> Outcome:
        sync_playwright = self._playwright()
        end_at = session.start + dt.timedelta(hours=3) - RESTOCK_END_BEFORE_END
        diagnostics: dict[str, object] = {
            "mode": "restock",
            "session_id": session.session_id,
            "session_start": session.start.isoformat(),
            "end_at": end_at.isoformat(),
        }
        with sync_playwright() as playwright:
            context = self._launch(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                captured_requests: list[dict[str, str]] = []

                def capture_request(request: Any) -> None:
                    if "act-api.cloud.tencent.com/dianshi/" in request.url:
                        captured_requests.append(
                            {
                                "url": request.url,
                                "post_data": request.post_data or "",
                            }
                        )

                page.on("request", capture_request)
                self._load_activity(page)
                csrf, signing_skey = self._auth_material(context)
                login = self._post_json(page, "/dianshi/check-login", {}, csrf=csrf)
                login_payload = login.get("payload")
                if not isinstance(login_payload, dict) or login_payload.get(
                    "code"
                ) not in (0, "0"):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "restock monitor could not verify activity login",
                    )

                card = self._target_card(page, timeout_seconds=30)
                self._wait_for_card_ready(card)
                activity_id = self._activity_id(captured_requests)
                schedule = self._post_json(
                    page,
                    SCHEDULE_PATH,
                    {
                        "activity_id": activity_id or DEFAULT_ACTIVITY_ID,
                        "preview": 0,
                        "seckill_type": "block",
                        "goods_type": "goods",
                        "days": 2,
                    },
                    csrf=csrf,
                )
                schedule_payload = schedule.get("payload")
                if not isinstance(schedule_payload, dict):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "restock monitor could not read the schedule",
                    )
                try:
                    offer = target_offer_from_schedule(
                        schedule_payload,
                        session_start=session.start,
                    )
                except ValueError as error:
                    return Outcome(
                        OutcomeKind.HALTED,
                        f"restock offer verification failed: {error}",
                    )
                runtime = self._prepare_runtime(
                    page,
                    csrf=csrf,
                    signing_skey=signing_skey,
                )
                if not runtime.get("ready"):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "restock signing runtime is unavailable",
                    )

                body = build_do_goods_payload(offer, page_url=page.url)
                check_body = {
                    "activity_id": offer.activity_id,
                    "goods": [
                        {
                            "act_id": offer.goods_id,
                            "region_id": [4],
                        }
                    ],
                    "preview": 0,
                }
                last_stock = False
                last_code: object = None
                consecutive_network_failures = 0
                diagnostics["polls"] = 0
                while now_china() < end_at:
                    try:
                        result = self._post_json(
                            page,
                            CHECK_AVAILABLE_PATH,
                            check_body,
                            csrf=csrf,
                        )
                    except StopFlow as stopped:
                        consecutive_network_failures += 1
                        LOGGER.warning(
                            "restock inventory request failed (%s/%s): %s",
                            consecutive_network_failures,
                            RESTOCK_MAX_CONSECUTIVE_NETWORK_FAILURES,
                            stopped.outcome.reason,
                        )
                        if page.is_closed() or (
                            consecutive_network_failures
                            >= RESTOCK_MAX_CONSECUTIVE_NETWORK_FAILURES
                        ):
                            return stopped.outcome
                        self.sleeper(
                            random.uniform(
                                RESTOCK_NETWORK_RETRY_MIN_SECONDS,
                                RESTOCK_NETWORK_RETRY_MAX_SECONDS,
                            )
                        )
                        continue
                    polls = int(diagnostics["polls"]) + 1
                    diagnostics["polls"] = polls
                    payload = result.get("payload")
                    code = payload.get("code") if isinstance(payload, dict) else None
                    consecutive_network_failures = 0
                    if code in {"NOT-LOGINED", "CSRF-ERROR"}:
                        return Outcome(
                            OutcomeKind.HALTED,
                            f"restock inventory API failed authentication: {code}",
                        )
                    stock = (
                        has_shanghai_stock(
                            payload,
                            goods_type=str(offer.goods_param.get("type", "")),
                        )
                        if isinstance(payload, dict)
                        else False
                    )
                    if not last_stock and stock:
                        LOGGER.info(
                            "restock stock changed to available at poll %s",
                            polls,
                        )
                    if stock and not last_stock:
                        LOGGER.info("restock detected for Shanghai target")
                        self._save_evidence(
                            page,
                            "restock-detected",
                            region=SHANGHAI_REGION_NAME,
                            total=offer.total,
                        )
                    if code != last_code or polls % 120 == 0:
                        LOGGER.info(
                            "restock poll %s code=%r stock=%s",
                            polls,
                            code,
                            stock,
                        )
                    last_code = code
                    last_stock = stock
                    if stock:
                        server_now_ms = int(time.time() * 1000)
                        server_performance_ms = 0.0
                        try:
                            now_result = self._post_json(
                                page,
                                "/dianshi/get-now",
                                {},
                                csrf=csrf,
                            )
                            now_payload = now_result.get("payload")
                            if isinstance(now_payload, dict):
                                data = now_payload.get("data")
                                candidate = (
                                    data.get("nowTime")
                                    if isinstance(data, dict)
                                    else data
                                )
                                if isinstance(candidate, int) and candidate > 0:
                                    server_now_ms = candidate
                                    server_performance_ms = float(
                                        now_result.get("browser_perf_ms") or 0
                                    )
                        except StopFlow:
                            LOGGER.warning(
                                "get-now failed during restock; using local clock"
                            )
                        burst = page.evaluate(
                            RESTOCK_BURST_SCRIPT,
                            {
                                "url": f"{API_HOST}{DO_GOODS_PATH}",
                                "body": body,
                                "csrf": csrf,
                                "maxAttempts": RESTOCK_BURST_ATTEMPTS,
                                "intervalMs": RESTOCK_BURST_INTERVAL_MS,
                                "serverNowMs": server_now_ms,
                                "performanceMs": server_performance_ms,
                            },
                        )
                        if not isinstance(burst, dict):
                            burst = {
                                "successes": 0,
                                "error": "unreadable restock burst response",
                            }
                        diagnostics["last_burst"] = burst
                        successes = int(burst.get("successes") or 0)
                        if successes:
                            deal_id = str(burst.get("first_deal_id") or "")
                            names_value = burst.get("first_deal_names")
                            names = (
                                tuple(str(item) for item in names_value)
                                if isinstance(names_value, list)
                                else ()
                            )
                            if deal_id:
                                self._navigate_to_order_check_values(
                                    page,
                                    deal_id=deal_id,
                                    deal_names=names,
                                )
                            self._save_evidence(
                                page,
                                "restock-order-created",
                                region=SHANGHAI_REGION_NAME,
                                total=offer.total,
                            )
                            return Outcome(
                                OutcomeKind.ORDER_CREATED,
                                f"restock returned {successes} order identifier(s)",
                                region=SHANGHAI_REGION_NAME,
                                total=offer.total,
                                orders=successes,
                            )
                        response_items = burst.get("responses")
                        saw_408 = (
                            isinstance(response_items, list)
                            and any(
                                isinstance(item, dict)
                                and str(item.get("code") or "") == "408"
                                for item in response_items
                            )
                        )
                        if saw_408:
                            cooldown = random.uniform(
                                RESTOCK_408_COOLDOWN_MIN_SECONDS,
                                RESTOCK_408_COOLDOWN_MAX_SECONDS,
                            )
                            LOGGER.warning(
                                "restock burst hit code 408; cooling down %.1f seconds",
                                cooldown,
                            )
                            self.sleeper(cooldown)
                            continue
                    self.sleeper(
                        random.uniform(
                            RESTOCK_POLL_MIN_SECONDS,
                            RESTOCK_POLL_MAX_SECONDS,
                        )
                    )
                return Outcome(
                    OutcomeKind.RETRYABLE_FAILURE,
                    "restock monitoring ended without an order",
                )
            except StopFlow as stopped:
                return stopped.outcome
            finally:
                context.close()
                self._save_run_diagnostics(diagnostics)


RESTOCK_BURST_SCRIPT = """async ({
  url,
  body,
  csrf,
  maxAttempts,
  intervalMs,
  serverNowMs,
  performanceMs
}) => {
  const runtime = window.__tencentPurchaseRuntime;
  if (!runtime) return {successes: 0, error: 'runtime missing'};
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const performanceBase = performance.now();
  const serverBase = Number(serverNowMs) || Date.now();
  const knownPerformanceBase = Number(performanceMs) || performanceBase;
  const currentServerMs = () =>
    serverBase + (performance.now() - knownPerformanceBase);
  try {
    const refreshed = await runtime.identity.JI();
    if (refreshed?.deviceToken) runtime.deviceToken = refreshed.deviceToken;
    runtime.deviceHeader = (await runtime.identity.c2()) || runtime.deviceHeader;
  } catch (_error) {}
  const findValue = (value, names, depth = 0) => {
    if (depth > 8 || value === null || value === undefined) return null;
    if (Array.isArray(value)) {
      for (const child of value) {
        const found = findValue(child, names, depth + 1);
        if (found !== null) return found;
      }
      return null;
    }
    if (typeof value !== 'object') return null;
    for (const [key, child] of Object.entries(value)) {
      if (names.includes(key) && child !== null && child !== '') return child;
      const found = findValue(child, names, depth + 1);
      if (found !== null) return found;
    }
    return null;
  };
  const attempts = [];
  const groups = new Map();
  for (let index = 0; index < Number(maxAttempts); index += 1) {
    attempts.push((async () => {
      const now = Math.round(currentServerMs());
      let signature;
      try {
        [signature] = await runtime.device.ge(
          ['post', , '/dianshi/do-goods', , {}, , body, , ,
            navigator.userAgent, runtime.signingSkey],
          now
        );
      } catch (error) {
        return {kind: 'network', error: String(error)};
      }
      const headers = {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRF-TOKEN': csrf,
        [runtime.signatureHeader]: String(signature || '')
      };
      if (runtime.deviceToken && runtime.deviceHeader) {
        headers[runtime.deviceHeader] = runtime.deviceToken;
      }
      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
          headers,
          body: JSON.stringify(body)
        });
        const contentType = response.headers.get('content-type') || '';
        const text = await response.text();
        let payload;
        try {
          payload = JSON.parse(text);
        } catch (error) {
          return {
            kind: 'parse',
            code: String(response.status),
            message: `non-JSON response content-type=${contentType} `
              + `prefix=${text.slice(0, 120)}`
          };
        }
        const code = String(payload?.code ?? '');
        const message = String(payload?.msg || payload?.message || '');
        const dealValue = findValue(payload, ['big_deal_no', 'deal_id', 'dealId']);
        const dealId = Array.isArray(dealValue) ? String(dealValue[0] || '') : String(dealValue || '');
        const success = code === '0' && dealId && dealId !== '0';
        return {kind: success ? 'success' : 'response', code, message, dealId};
      } catch (error) {
        return {kind: 'network', error: String(error)};
      }
    })());
    await sleep(Number(intervalMs));
  }
  const responses = await Promise.all(attempts);
  const successes = responses.filter(item => item.kind === 'success');
  return {
    attempts: responses.length,
    successes: successes.length,
    first_deal_id: successes[0]?.dealId || '',
    first_deal_names: [],
    responses: responses.map(item => ({
      kind: item.kind,
      code: item.code || '',
      message: (item.message || item.error || '').slice(0, 200)
    }))
  };
}"""
