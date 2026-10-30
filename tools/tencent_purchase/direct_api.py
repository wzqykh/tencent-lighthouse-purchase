"""Single-shot Tencent activity API purchase flow inside the logged-in browser."""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Callable
from urllib.parse import urlencode

from .api import (
    API_HOST,
    CHECK_LOGIN_PATH,
    DEFAULT_ACTIVITY_ID,
    DO_GOODS_PATH,
    ORDER_CHECK_URL,
    SCHEDULE_PATH,
    SHANGHAI_REGION_NAME,
    csrf_token_from_skey,
    build_do_goods_payload,
    target_offer_from_schedule,
)
from .browser import StopFlow, TencentPurchaseBrowser
from .checks import card_snapshot_problems
from .outcome import Outcome, OutcomeKind
from .schedule import SaleSession, now_china, wait_until
from .settings import RunnerSettings
from .state import DayState

LOGGER = logging.getLogger(__name__)

BURST_START_BEFORE = dt.timedelta(minutes=3)
BURST_ACCELERATE_BEFORE = dt.timedelta(seconds=30)
BURST_STOP_AFTER = dt.timedelta(seconds=5)
MAX_BURST_ATTEMPTS = 2_000


class TencentDirectApiBrowser(TencentPurchaseBrowser):
    def __init__(
        self,
        settings: RunnerSettings,
        *,
        clock: Callable[[], dt.datetime] = now_china,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(settings, clock=clock, sleeper=sleeper)

    def preflight(self, session: SaleSession) -> dict[str, object]:
        """Verify login, offer, price and signing runtime without submitting."""

        sync_playwright = self._playwright()
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
                result: dict[str, object] = {
                    "session_id": session.session_id,
                    "session_start": session.start.isoformat(),
                    "logged_in": False,
                    "page_auth_diagnostics": self._page_auth_diagnostics(page),
                }
                try:
                    csrf, signing_skey = self._auth_material(context)
                except StopFlow as stopped:
                    result["error"] = stopped.outcome.reason
                    return result
                login = self._post_json(page, CHECK_LOGIN_PATH, {}, csrf=csrf)
                login_payload = login.get("payload")
                result["login_response"] = (
                    {
                        "code": login_payload.get("code"),
                        "msg": login_payload.get("msg"),
                    }
                    if isinstance(login_payload, dict)
                    else {}
                )
                if (
                    login.get("status") != 200
                    or not isinstance(login_payload, dict)
                    or login_payload.get("code") not in (0, "0")
                ):
                    result["error"] = "activity API login rejected"
                    return result
                result["logged_in"] = True

                card = self._target_card(page, timeout_seconds=20)
                result["card"] = self._wait_for_card_ready(card)
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
                if schedule.get("status") != 200 or not isinstance(
                    schedule_payload, dict
                ):
                    result["error"] = "schedule response is unreadable"
                    return result
                try:
                    offer = target_offer_from_schedule(
                        schedule_payload,
                        session_start=session.start,
                    )
                except ValueError as error:
                    result["error"] = str(error)
                    return result
                result["offer"] = {
                    "activity_id": offer.activity_id,
                    "session_id": offer.session_id,
                    "goods_id": offer.goods_id,
                    "region": SHANGHAI_REGION_NAME,
                    "total": str(offer.total),
                    "image_id": offer.goods_param["imageId"],
                }
                try:
                    result["runtime"] = self._prepare_runtime(
                        page,
                        csrf=csrf,
                        signing_skey=signing_skey,
                    )
                except Exception as error:
                    result["error"] = (
                        "signing runtime initialization failed: "
                        f"{type(error).__name__}"
                    )
                return result
            finally:
                context.close()

    def _page_auth_diagnostics(self, page: Any) -> dict[str, object]:
        try:
            return page.evaluate(
                """async () => {
                  const visible = el => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width > 0
                      && rect.height > 0;
                  };
                  const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                  const buttons = [...document.querySelectorAll('a,button')]
                    .filter(visible)
                    .map(el => clean(el.innerText || el.textContent))
                    .filter(Boolean)
                    .slice(0, 400);
                  let databases = [];
                  if (indexedDB.databases) {
                    try {
                      databases = (await indexedDB.databases())
                        .map(database => database.name)
                        .filter(Boolean)
                        .slice(0, 80);
                    } catch (_error) {}
                  }
                  const relevant = names => names
                    .filter(name => /auth|login|token|user|uin|qcloud|csrf/i.test(name))
                    .sort()
                    .slice(0, 100);
                  return {
                    title: document.title,
                    url_origin: location.origin,
                    has_login_prompt: buttons.some(
                      text => text === '登录' || text.includes('扫码登录')
                    ),
                    has_console_entry: buttons.some(
                      text => text.includes('控制台') || text.includes('费用中心')
                    ),
                    local_storage_keys: relevant(Object.keys(localStorage)),
                    session_storage_keys: relevant(Object.keys(sessionStorage)),
                    indexed_db_names: databases
                  };
                }"""
            )
        except Exception as error:
            return {"error": type(error).__name__}

    def run(
        self,
        session: SaleSession,
        state: DayState,
        *,
        save_state: Callable[[DayState], None],
    ) -> Outcome:
        sync_playwright = self._playwright()
        diagnostics: dict[str, object] = {
            "mode": "direct_activity_api",
            "session_id": session.session_id,
            "session_start": session.start.isoformat(),
            "_started_monotonic": time.monotonic(),
            "timing_ms": {},
        }
        with sync_playwright() as playwright:
            context = self._launch(playwright)
            self._mark_timing(diagnostics, "browser_launched")
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                captured_requests: list[dict[str, str]] = []

                def capture_request(request: Any) -> None:
                    if "act-api.cloud.tencent.com/dianshi/" not in request.url:
                        return
                    captured_requests.append(
                        {
                            "url": request.url,
                            "post_data": request.post_data or "",
                        }
                    )

                page.on("request", capture_request)
                wait_until(
                    session.browser_ready_at,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    on_wait=lambda remaining: LOGGER.info(
                        "waiting %.1f seconds to prepare direct purchase", remaining
                    ),
                )
                self._load_activity(page)
                self._mark_timing(diagnostics, "activity_loaded")

                try:
                    csrf, signing_skey = self._auth_material(context)
                except StopFlow as stopped:
                    return stopped.outcome

                login = self._post_json(page, CHECK_LOGIN_PATH, {}, csrf=csrf)
                login_payload = login.get("payload")
                if not isinstance(login_payload, dict):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "the activity login check returned an unreadable response",
                    )
                diagnostics["login_code"] = login_payload.get("code")
                if login.get("status") != 200 or login_payload.get("code") not in (
                    0,
                    "0",
                ):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "the Tencent activity API rejected the current login; re-run login setup",
                    )
                self._mark_timing(diagnostics, "login_verified")

                card = self._target_card(page, timeout_seconds=20)
                snapshot = self._wait_for_card_ready(card)
                problems = card_snapshot_problems(snapshot)
                if problems:
                    return Outcome(OutcomeKind.HALTED, "; ".join(problems))
                self._mark_timing(diagnostics, "target_verified")

                captured_activity_id = self._activity_id(captured_requests)
                schedule = self._post_json(
                    page,
                    SCHEDULE_PATH,
                    {
                        "activity_id": captured_activity_id or DEFAULT_ACTIVITY_ID,
                        "preview": 0,
                        "seckill_type": "block",
                        "goods_type": "goods",
                        "days": 2,
                    },
                    csrf=csrf,
                )
                schedule_payload = schedule.get("payload")
                if schedule.get("status") != 200 or not isinstance(
                    schedule_payload, dict
                ):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "the activity schedule returned an unreadable response",
                    )
                schedule_data = schedule_payload.get("data")
                if not isinstance(schedule_data, dict):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "the activity schedule response has no data",
                    )
                schedule["server_time_ms"] = schedule_data.get("nowTime")
                try:
                    offer = target_offer_from_schedule(
                        schedule_payload,
                        session_start=session.start,
                    )
                except ValueError as error:
                    return Outcome(
                        OutcomeKind.HALTED,
                        f"could not verify the current activity offer: {error}",
                    )
                if (
                    captured_activity_id
                    and captured_activity_id != offer.activity_id
                ):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "captured activity id and verified schedule activity id differ",
                    )
                diagnostics["activity_id"] = offer.activity_id
                diagnostics["session_number"] = offer.session_id
                diagnostics["goods_id"] = offer.goods_id
                diagnostics["server_time_ms"] = schedule["server_time_ms"]
                diagnostics["browser_performance_ms"] = schedule["browser_perf_ms"]
                self._mark_timing(diagnostics, "schedule_verified")

                try:
                    runtime = self._prepare_runtime(
                        page,
                        csrf=csrf,
                        signing_skey=signing_skey,
                    )
                except Exception as error:
                    return Outcome(
                        OutcomeKind.HALTED,
                        "could not initialize the page signing runtime: "
                        f"{type(error).__name__}",
                    )
                diagnostics["runtime"] = runtime
                if not runtime.get("ready"):
                    return Outcome(
                        OutcomeKind.HALTED,
                        f"the page signing runtime is unavailable: {runtime.get('reason', '')}",
                    )
                self._mark_timing(diagnostics, "api_runtime_ready")

                body = build_do_goods_payload(offer, page_url=page.url)
                wait_until(
                    session.start - BURST_START_BEFORE,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    on_wait=lambda remaining: LOGGER.info(
                        "waiting %.1f seconds before the burst window", remaining
                    ),
                )
                state.mark_submit_intent()
                state.mark_api_request_started()
                save_state(state)
                self._mark_timing(diagnostics, "submit_intent_persisted")

                try:
                    raw_result = page.evaluate(
                        BURST_SUBMIT_SCRIPT,
                        {
                            "url": f"{API_HOST}{DO_GOODS_PATH}",
                            "getNowUrl": f"{API_HOST}/dianshi/get-now",
                            "body": body,
                            "csrf": csrf,
                            "targetEpochMs": int(session.start.timestamp() * 1000),
                            "nowTimeMs": schedule["server_time_ms"],
                            "performanceMs": schedule["browser_perf_ms"],
                            "maxAttempts": MAX_BURST_ATTEMPTS,
                        },
                    )
                except Exception as error:
                    state.mark_api_response_received()
                    save_state(state)
                    diagnostics["submit_error"] = type(error).__name__
                    return Outcome(
                        OutcomeKind.UNKNOWN,
                        "the API burst ended without a readable result",
                    )

                state.mark_api_response_received()
                save_state(state)
                if not isinstance(raw_result, dict):
                    return Outcome(
                        OutcomeKind.UNKNOWN,
                        "the API burst returned an unreadable summary",
                    )
                if not raw_result.get("started"):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "the API burst could not start: "
                        f"{raw_result.get('error', 'unknown setup error')}",
                    )

                diagnostics["burst"] = {
                    "attempts": raw_result.get("attempts", 0),
                    "responses": raw_result.get("responses", 0),
                    "in_flight_at_end": raw_result.get("in_flight_at_end", 0),
                    "successes": raw_result.get("successes", 0),
                    "accelerated": bool(raw_result.get("accelerated")),
                    "transition_server_ms": raw_result.get("transition_server_ms"),
                    "baseline": raw_result.get("baseline"),
                    "transition": raw_result.get("transition"),
                    "response_groups": raw_result.get("response_groups", []),
                    "network_errors": raw_result.get("network_errors", 0),
                    "parse_errors": raw_result.get("parse_errors", 0),
                    "trace": raw_result.get("trace", []),
                }
                successes = int(raw_result.get("successes") or 0)
                if successes > 0:
                    state.mark_order_created()
                    state.mark_payment_stage()
                    save_state(state)
                    self._mark_timing(diagnostics, "order_created_result")
                    deal_id = str(raw_result.get("first_deal_id") or "")
                    deal_names_value = raw_result.get("first_deal_names")
                    deal_names = (
                        tuple(str(item) for item in deal_names_value)
                        if isinstance(deal_names_value, list)
                        else ()
                    )
                    if deal_id:
                        self._navigate_to_order_check_values(
                            page,
                            deal_id=deal_id,
                            deal_names=deal_names,
                        )
                    self._save_evidence(
                        page,
                        "api-order-created",
                        region=SHANGHAI_REGION_NAME,
                        total=offer.total,
                    )
                    return Outcome(
                        OutcomeKind.ORDER_CREATED,
                        f"the direct order API returned {successes} order identifier(s)",
                        region=SHANGHAI_REGION_NAME,
                        total=offer.total,
                        orders=successes,
                    )

                ambiguous = int(raw_result.get("ambiguous") or 0)
                fatal = int(raw_result.get("fatal") or 0)
                no_order = int(raw_result.get("no_order") or 0)
                network_errors = int(raw_result.get("network_errors") or 0)
                parse_errors = int(raw_result.get("parse_errors") or 0)
                if fatal or network_errors or parse_errors or ambiguous:
                    return Outcome(
                        OutcomeKind.UNKNOWN,
                        "the API burst had ambiguous or fatal responses "
                        f"(ambiguous={ambiguous}, fatal={fatal}, "
                        f"network={network_errors}, parse={parse_errors})",
                    )
                if no_order:
                    return Outcome(
                        OutcomeKind.RETRYABLE_FAILURE,
                        f"all {int(raw_result.get('responses') or 0)} API responses "
                        "were explicit no-order results",
                    )
                return Outcome(
                    OutcomeKind.HALTED,
                    "no order API request was dispatched during the burst window",
                )
            except StopFlow as stopped:
                return stopped.outcome
            finally:
                context.close()
                self._mark_timing(diagnostics, "browser_closed")
                self._save_run_diagnostics(diagnostics)

    def _auth_material(self, context: Any) -> tuple[str, str]:
        cookies = {
            str(cookie.get("name", "")).lower(): str(cookie.get("value", ""))
            for cookie in context.cookies()
            if cookie.get("value")
        }
        skey = cookies.get("skey", "")
        p_skey = cookies.get("p_skey", "")
        if skey:
            return csrf_token_from_skey(skey), skey
        if p_skey:
            return csrf_token_from_skey(p_skey), p_skey
        main_csrf = cookies.get("qcmaincsrftoken", "")
        if main_csrf:
            return main_csrf, ""
        raise StopFlow(
            Outcome(
                OutcomeKind.HALTED,
                "the automation profile has no activity login material; log in again",
            )
        )

    def _post_json(
        self,
        page: Any,
        path: str,
        payload: dict[str, object],
        *,
        csrf: str,
    ) -> dict[str, Any]:
        try:
            result = page.evaluate(
                """async ({url, payload, csrf}) => {
              const response = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                cache: 'no-store',
                headers: {
                  'Content-Type': 'application/json',
                  'X-Requested-With': 'XMLHttpRequest',
                  'X-CSRF-TOKEN': csrf
                },
                body: JSON.stringify(payload)
              });
              const text = await response.text();
              let parsed;
              try {
                parsed = JSON.parse(text);
              } catch (error) {
                parsed = {
                  parse_error: String(error),
                  text_prefix: text.slice(0, 300)
                };
              }
              return {
                status: response.status,
                payload: parsed,
                browser_perf_ms: performance.now(),
                browser_wall_ms: Date.now()
              };
            }""",
                {
                    "url": f"{API_HOST}{path}",
                    "payload": payload,
                    "csrf": csrf,
                },
            )
        except Exception as error:
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    f"activity API request {path} failed: {type(error).__name__}",
                )
            ) from error
        if not isinstance(result, dict):
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, f"non-object response from {path}")
            )
        return result

    def _activity_id(self, requests: list[dict[str, str]]) -> int | None:
        from .api import activity_id_from_requests

        return activity_id_from_requests(requests)

    def _prepare_runtime(
        self,
        page: Any,
        *,
        csrf: str,
        signing_skey: str,
    ) -> dict[str, object]:
        result = page.evaluate(
            """async ({csrf, signingSkey}) => {
              try {
                let requireModule = null;
                const chunkName = 'webpackChunk_tencent_qcact_ssr';
                const chunkId = `tp-purchase-${Date.now()}`;
                window[chunkName].push([
                  [chunkId],
                  {},
                  requireValue => { requireModule = requireValue; }
                ]);
                if (!requireModule) {
                  return {ready: false, reason: 'webpack require unavailable'};
                }
                const device = requireModule(8579).dm;
                const identity = requireModule(2937);
                const signatureHeader = await device.he();
                const deviceHeader = await identity.c2();
                const deviceData = await identity.JI();
                window.__tencentPurchaseRuntime = {
                  submitted: false,
                  device,
                  identity,
                  signatureHeader,
                  deviceHeader,
                  deviceToken: deviceData?.deviceToken || '',
                  csrf,
                  signingSkey
                };
                return {
                  ready: true,
                  signature_header: signatureHeader,
                  device_header: deviceHeader,
                  has_device_token: !!deviceData?.deviceToken
                };
              } catch (error) {
                return {ready: false, reason: String(error).slice(0, 300)};
              }
            }""",
            {"csrf": csrf, "signingSkey": signing_skey},
        )
        return result if isinstance(result, dict) else {
            "ready": False,
            "reason": "invalid runtime response",
        }

    def _navigate_to_order_check_values(
        self,
        page: Any,
        *,
        deal_id: str,
        deal_names: tuple[str, ...],
    ) -> None:
        if not deal_id:
            return
        query = urlencode(
            {
                "bigDealId": deal_id,
                "dealName": deal_names[0] if deal_names else deal_id,
                "s_url": page.url,
                "serviceFrom": "activity",
            }
        )
        try:
            page.goto(
                f"{ORDER_CHECK_URL}?{query}",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            page.wait_for_timeout(1_000)
        except Exception:
            LOGGER.warning(
                "order was created, but the confirmation page did not open",
                exc_info=True,
            )


BURST_SUBMIT_SCRIPT = """async ({
  url,
  getNowUrl,
  body,
  csrf,
  targetEpochMs,
  nowTimeMs,
  performanceMs,
  maxAttempts
}) => {
  const runtime = window.__tencentPurchaseRuntime;
  if (!runtime) {
    return {started: false, error: 'page signing runtime is missing'};
  }

  const sleep = milliseconds => new Promise(
    resolve => setTimeout(resolve, milliseconds)
  );
  const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
  const responseGroups = new Map();
  const trace = [];
  const inFlight = new Set();
  const successfulDealIds = [];
  let attempts = 0;
  let responses = 0;
  let networkErrors = 0;
  let parseErrors = 0;
  let noOrder = 0;
  let ambiguous = 0;
  let fatal = 0;
  let successSeen = false;
  let firstDealId = '';
  let firstDealNames = [];
  let accelerated = false;
  let transition = null;

  let serverNowMs = Number(nowTimeMs) || Date.now();
  let serverPerformanceMs = Number(performanceMs) || performance.now();
  try {
    const nowResponse = await fetch(getNowUrl, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRF-TOKEN': csrf
      },
      body: '{}'
    });
    const nowPayload = await nowResponse.json();
    const nowValue = typeof nowPayload?.data === 'number'
      ? nowPayload.data
      : Number(nowPayload?.data?.nowTime);
    if (nowResponse.ok && Number.isFinite(nowValue) && nowValue > 0) {
      serverNowMs = nowValue;
      serverPerformanceMs = performance.now();
    }
  } catch (_error) {
    // The schedule timestamp is the bounded fallback clock.
  }

  const currentServerMs = () =>
    serverNowMs + (performance.now() - serverPerformanceMs);
  const targetServerMs = Number(targetEpochMs);
  const highSpeedServerMs = targetServerMs - 30_000;
  const hardStopServerMs = targetServerMs + 5_000;
  if (!Number.isFinite(targetServerMs)) {
    return {started: false, error: 'target timestamp is invalid'};
  }

  const targetCard = () => [...document.querySelectorAll(
    '#MS .uno3-buy-card-seckill'
  )].find(element => {
    const text = clean(element.innerText);
    return text.includes('轻量应用服务器') && text.includes('4核4G3M');
  }) || null;

  const snapshot = () => {
    const card = targetCard();
    if (!card) {
      return {
        server_ms: Math.round(currentServerMs()),
        exists: false,
        signature: 'missing'
      };
    }
    const cta = card.querySelector('.uno3-buy-card__btn');
    const progress = card.querySelector(
      '.uno3-buy-card__progress-bar-inner'
    );
    const wrapper = cta?.closest('.uno3-buy-card__btn-progress');
    const ctaText = clean(cta?.innerText);
    const progressText = progress?.style?.width || '';
    const disabled = !!(cta?.disabled)
      || !!(wrapper?.classList?.contains('is-disabled'));
    return {
      server_ms: Math.round(currentServerMs()),
      exists: true,
      cta: ctaText,
      progress: progressText,
      disabled,
      signature: `${ctaText}|${progressText}|${disabled}`
    };
  };

  const baseline = snapshot();
  let lastSignature = baseline.signature;
  const traceTimer = setInterval(() => {
    const state = snapshot();
    trace.push(state);
    if (!accelerated && state.signature !== lastSignature) {
      accelerated = true;
      const card = targetCard();
      transition = {
        server_ms: state.server_ms,
        state,
        html: card ? card.outerHTML.slice(0, 20_000) : ''
      };
    }
    lastSignature = state.signature;
  }, 100);

  const findNamedValue = (value, names, depth = 0) => {
    if (depth > 8 || value === null || value === undefined) return null;
    if (Array.isArray(value)) {
      for (const child of value) {
        const found = findNamedValue(child, names, depth + 1);
        if (found !== null) return found;
      }
      return null;
    }
    if (typeof value !== 'object') return null;
    for (const [key, child] of Object.entries(value)) {
      if (names.includes(key) && child !== null && child !== '') return child;
      const found = findNamedValue(child, names, depth + 1);
      if (found !== null) return found;
    }
    return null;
  };

  const recordGroup = (kind, code, message) => {
    const key = `${kind}|${code}|${message}`;
    const existing = responseGroups.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      responseGroups.set(key, {
        kind,
        code,
        message: message.slice(0, 300),
        count: 1
      });
    }
  };

  const sendAttempt = () => {
    if (
      successSeen
      || attempts >= Number(maxAttempts)
      || currentServerMs() >= hardStopServerMs
    ) {
      return;
    }
    attempts += 1;
    const promise = (async () => {
      const nowTime = Math.round(currentServerMs());
      let signature;
      try {
        [signature] = await runtime.device.ge(
          [
            'post', , '/dianshi/do-goods', , {}, ,
            body, , , navigator.userAgent, runtime.signingSkey
          ],
          nowTime
        );
      } catch (error) {
        networkErrors += 1;
        recordGroup('network', 'SIGN', String(error));
        return;
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

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 12_000);
      let response;
      try {
        response = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
          headers,
          body: JSON.stringify(body),
          signal: controller.signal
        });
      } catch (error) {
        networkErrors += 1;
        recordGroup('network', 'FETCH', String(error));
        return;
      } finally {
        clearTimeout(timeout);
      }

      responses += 1;
      const text = await response.text();
      let payload;
      try {
        payload = JSON.parse(text);
      } catch (error) {
        parseErrors += 1;
        recordGroup('ambiguous', 'PARSE', String(error));
        return;
      }
      const code = String(payload?.code ?? '');
      const message = clean(payload?.msg || payload?.message || '');
      if (!response.ok || !payload || typeof payload !== 'object') {
        ambiguous += 1;
        recordGroup('ambiguous', code || String(response.status), message);
        return;
      }
      if (code === '0') {
        const dealValue = findNamedValue(
          payload,
          ['big_deal_no', 'deal_id', 'dealId']
        );
        const dealId = Array.isArray(dealValue)
          ? String(dealValue[0] || '')
          : dealValue === null ? '' : String(dealValue);
        if (dealId && dealId !== '0') {
          successSeen = true;
          successfulDealIds.push(dealId);
          if (!firstDealId) {
            firstDealId = dealId;
            const nameValue = findNamedValue(
              payload,
              ['deal_names', 'dealNames']
            );
            firstDealNames = Array.isArray(nameValue)
              ? nameValue.map(String)
              : [];
          }
          recordGroup('success', code, message);
          return;
        }
        ambiguous += 1;
        recordGroup('ambiguous', code, message || 'code 0 without deal id');
        return;
      }
      if (
        code === '1101001'
        || code === '408'
        || /尚未开始|还没开始|未开始|已售罄|已抢光|库存不足|无库存/.test(message)
      ) {
        noOrder += 1;
        recordGroup('no_order', code, message);
        return;
      }
      if (
        /NOT-LOGINED|CSRF-ERROR|不满足活动参与条件|不满足购买条件|账号风险|实名|验证码|face|captcha/i
          .test(`${code} ${message}`)
      ) {
        fatal += 1;
        recordGroup('fatal', code, message);
        return;
      }
      ambiguous += 1;
      recordGroup('ambiguous', code, message);
    })().finally(() => {
      inFlight.delete(promise);
    });
    inFlight.add(promise);
  };

  let nextAttemptAt = 0;
  while (currentServerMs() < hardStopServerMs && !successSeen) {
    const now = currentServerMs();
    const highSpeed = accelerated || now >= highSpeedServerMs;
    if (now >= nextAttemptAt) {
      sendAttempt();
      nextAttemptAt = now + (highSpeed ? 100 : 1_000);
    }
    await sleep(20);
  }

  if (inFlight.size > 0) {
    await Promise.race([
      Promise.allSettled([...inFlight]),
      sleep(12_000)
    ]);
  }
  clearInterval(traceTimer);
  return {
    started: true,
    done: true,
    attempts,
    responses,
    in_flight_at_end: inFlight.size,
    successes: successfulDealIds.length,
    successful_deal_count: successfulDealIds.length,
    first_deal_id: firstDealId,
    first_deal_names: firstDealNames,
    accelerated,
    transition_server_ms: transition?.server_ms || null,
    baseline,
    transition,
    response_groups: [...responseGroups.values()],
    network_errors: networkErrors,
    parse_errors: parseErrors,
    no_order: noOrder,
    ambiguous,
    fatal,
    trace
  };
}"""


def run_api_browser(
    settings: RunnerSettings,
    session: SaleSession,
    state: DayState,
    *,
    save_state: Callable[[DayState], None],
) -> Outcome:
    return TencentDirectApiBrowser(settings).run(
        session,
        state,
        save_state=save_state,
    )
