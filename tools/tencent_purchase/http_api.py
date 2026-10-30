"""Direct HTTPS activity API runner.

The browser is used only to load the authenticated activity page, obtain the
current session material in memory and execute Tencent's current signing
function. Order requests are sent by an HTTP/2 client instead of page fetch.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from .api import (
    API_HOST,
    CHECK_LOGIN_PATH,
    DEFAULT_ACTIVITY_ID,
    DO_GOODS_PATH,
    SCHEDULE_PATH,
    SHANGHAI_REGION_NAME,
    ApiDisposition,
    TargetOffer,
    build_do_goods_payload,
    classify_do_goods_response,
    target_offer_from_schedule,
)
from .direct_api import TencentDirectApiBrowser
from .outcome import Outcome, OutcomeKind
from .schedule import SaleSession, wait_until
from .state import DayState

LOGGER = logging.getLogger(__name__)

DIRECT_CONNECTION_WARMUP_BEFORE = dt.timedelta(seconds=20)
DIRECT_CLOCK_SAMPLE_BEFORE = dt.timedelta(seconds=10)
DIRECT_CLOCK_SAMPLES = 2
DIRECT_START_BEFORE = dt.timedelta(milliseconds=1_500)
DIRECT_STOP_AFTER = dt.timedelta(seconds=30)
DIRECT_BATCH_SIZE = 10
DIRECT_BATCH_GAP_MIN_SECONDS = 0.35
DIRECT_BATCH_GAP_MAX_SECONDS = 0.60
DIRECT_408_BACKOFF_SECONDS = 1.5
DIRECT_MAX_ATTEMPTS = 200
DIRECT_REQUEST_TIMEOUT_SECONDS = 3.0


@dataclass
class DirectHttpMaterial:
    client: Any
    csrf: str
    signing_skey: str
    page_url: str
    user_agent: str
    runtime: dict[str, object]
    offer: TargetOffer
    server_now_ms: int
    prepared_monotonic: float


@dataclass(frozen=True)
class ClockSample:
    server_now_ms: int
    rtt_ms: float
    received_monotonic: float

    @property
    def corrected_server_ms(self) -> int:
        return round(self.server_now_ms + self.rtt_ms / 2)


def best_clock_sample(samples: list[ClockSample]) -> ClockSample | None:
    if not samples:
        return None
    return min(samples, key=lambda sample: sample.rtt_ms)


def cookie_header(cookies: list[dict[str, object]]) -> str:
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name or not value or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def server_now_from_payload(payload: object) -> int | None:
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0"):
        return None
    data = payload.get("data")
    if isinstance(data, int):
        return data if data > 0 else None
    if isinstance(data, dict):
        value = data.get("nowTime")
        if isinstance(value, int) and value > 0:
            return value
    return None


class TencentHttpApiBrowser(TencentDirectApiBrowser):
    """Run Tencent's activity API without page-level fetch in the hot path."""

    def __init__(
        self,
        settings,
        *,
        clock: Callable[[], dt.datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            settings,
            clock=clock or self._default_clock(),
            sleeper=sleeper,
        )

    @staticmethod
    def _default_clock() -> Callable[[], dt.datetime]:
        from .schedule import now_china

        return now_china

    def _prepare_http(
        self,
        page: Any,
        context: Any,
        *,
        session: SaleSession,
    ) -> DirectHttpMaterial:
        import httpx

        csrf, signing_skey = self._auth_material(context)
        cookies = cookie_header(
            context.cookies([API_HOST, "https://cloud.tencent.com"])
        )
        if not cookies:
            raise ValueError("no authenticated cookies for the activity API")
        user_agent = str(page.evaluate("() => navigator.userAgent"))
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Cookie": cookies,
            "Origin": "https://cloud.tencent.com",
            "Referer": page.url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": user_agent,
            "X-CSRF-TOKEN": csrf,
            "X-Requested-With": "XMLHttpRequest",
        }
        client = httpx.Client(
            http2=True,
            timeout=DIRECT_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        )

        def post(path: str, payload: dict[str, object]) -> object:
            text = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            response = client.post(f"{API_HOST}{path}", content=text)
            response.raise_for_status()
            return response.json()

        login_payload = post(CHECK_LOGIN_PATH, {})
        if not isinstance(login_payload, dict) or login_payload.get("code") not in (
            0,
            "0",
        ):
            raise ValueError("direct HTTP activity login check failed")

        captured: list[dict[str, str]] = []

        def capture(request: Any) -> None:
            if "act-api.cloud.tencent.com/dianshi/" in request.url:
                captured.append({"post_data": request.post_data or ""})

        page.on("request", capture)
        schedule_payload = post(
            SCHEDULE_PATH,
            {
                "activity_id": DEFAULT_ACTIVITY_ID,
                "preview": 0,
                "seckill_type": "block",
                "goods_type": "goods",
                "days": 2,
            },
        )
        offer = target_offer_from_schedule(
            schedule_payload,
            session_start=session.start,
        )
        runtime = self._prepare_runtime(
            page,
            csrf=csrf,
            signing_skey=signing_skey,
        )
        if not runtime.get("ready"):
            client.close()
            raise ValueError("page signing runtime is unavailable")
        return DirectHttpMaterial(
            client=client,
            csrf=csrf,
            signing_skey=signing_skey,
            page_url=page.url,
            user_agent=user_agent,
            runtime=runtime,
            offer=offer,
            server_now_ms=offer.server_now_ms,
            prepared_monotonic=time.monotonic(),
        )

    def preflight(self, session: SaleSession) -> dict[str, object]:
        sync_playwright = self._playwright()
        with sync_playwright() as playwright:
            context = self._launch(playwright, headless=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                self._load_activity(page)
                material = self._prepare_http(page, context, session=session)
                try:
                    signature, _ = self._sign_at(
                        page,
                        {},
                        now_ms=material.server_now_ms,
                    )
                    return {
                        "mode": "direct_http_api",
                        "session_id": session.session_id,
                        "session_start": session.start.isoformat(),
                        "logged_in": True,
                        "offer": {
                            "activity_id": material.offer.activity_id,
                            "session_id": material.offer.session_id,
                            "goods_id": material.offer.goods_id,
                            "region": SHANGHAI_REGION_NAME,
                            "total": str(material.offer.total),
                        },
                        "runtime": {
                            "ready": True,
                            "signature_header": material.runtime.get(
                                "signature_header"
                            ),
                            "device_header": material.runtime.get("device_header"),
                            "signature_length": len(signature),
                        },
                    }
                finally:
                    material.client.close()
            finally:
                context.close()

    def probe_do_goods(self, session: SaleSession) -> dict[str, object]:
        """Send one signed order request through direct HTTP without state changes."""

        sync_playwright = self._playwright()
        with sync_playwright() as playwright:
            context = self._launch(playwright, headless=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                self._load_activity(page)
                material = self._prepare_http(page, context, session=session)
                try:
                    body = build_do_goods_payload(
                        material.offer,
                        page_url=material.page_url,
                    )
                    remote_now = self._direct_json(
                        material.client,
                        "/dianshi/get-now",
                        {},
                    )
                    now_ms = server_now_from_payload(remote_now)
                    if now_ms is None:
                        now_ms = material.server_now_ms
                    signature, body_text = self._sign_at(
                        page,
                        body,
                        now_ms=now_ms,
                    )
                    result = self._send_signed(
                        material.client,
                        material,
                        body_text,
                        signature,
                    )
                    return {
                        "mode": "direct_http_api_probe",
                        "session_id": session.session_id,
                        "offer": {
                            "activity_id": material.offer.activity_id,
                            "session_id": material.offer.session_id,
                            "region": SHANGHAI_REGION_NAME,
                            "total": str(material.offer.total),
                        },
                        "result": result,
                    }
                finally:
                    material.client.close()
            finally:
                context.close()

    def run(
        self,
        session: SaleSession,
        state: DayState,
        *,
        save_state: Callable[[DayState], None],
    ) -> Outcome:
        sync_playwright = self._playwright()
        diagnostics: dict[str, object] = {
            "mode": "direct_http_api",
            "session_id": session.session_id,
            "session_start": session.start.isoformat(),
            "_started_monotonic": time.monotonic(),
            "timing_ms": {},
        }
        with sync_playwright() as playwright:
            context = self._launch(playwright, headless=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                self._load_activity(page)
                material = self._prepare_http(page, context, session=session)
                diagnostics["runtime"] = {
                    key: value
                    for key, value in material.runtime.items()
                    if key
                    in {
                        "ready",
                        "signature_header",
                        "device_header",
                        "has_device_token",
                    }
                }
                diagnostics["offer"] = {
                    "activity_id": material.offer.activity_id,
                    "session_id": material.offer.session_id,
                    "goods_id": material.offer.goods_id,
                    "region": SHANGHAI_REGION_NAME,
                    "total": str(material.offer.total),
                }
                self._mark_timing(diagnostics, "http_api_ready")
                wait_until(
                    session.start - DIRECT_CONNECTION_WARMUP_BEFORE,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    on_wait=lambda remaining: LOGGER.info(
                        "direct HTTP API connection warmup in %.1f seconds",
                        remaining,
                    ),
                )
                warmup_sample = self._sample_clock(material.client)
                LOGGER.info(
                    "direct HTTP API connection warmup completed rtt=%sms",
                    (
                        round(warmup_sample.rtt_ms, 1)
                        if warmup_sample is not None
                        else "unknown"
                    ),
                )

                wait_until(
                    session.start - DIRECT_CLOCK_SAMPLE_BEFORE,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    on_wait=lambda remaining: LOGGER.info(
                        "direct HTTP API clock calibration in %.1f seconds",
                        remaining,
                    ),
                )
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=DIRECT_CLOCK_SAMPLES
                ) as sampler:
                    samples = [
                        sample
                        for sample in sampler.map(
                            self._sample_clock,
                            [material.client] * DIRECT_CLOCK_SAMPLES,
                        )
                        if sample is not None
                    ]
                best_sample = best_clock_sample(samples)
                if best_sample is not None:
                    server_base = best_sample.corrected_server_ms
                    monotonic_base = best_sample.received_monotonic
                else:
                    monotonic_base = time.monotonic()
                    server_base = round(
                        material.server_now_ms
                        + (monotonic_base - material.prepared_monotonic) * 1000
                    )
                diagnostics["clock"] = {
                    "warmup_rtt_ms": (
                        round(warmup_sample.rtt_ms, 2)
                        if warmup_sample is not None
                        else None
                    ),
                    "samples_rtt_ms": [
                        round(sample.rtt_ms, 2) for sample in samples
                    ],
                    "selected_rtt_ms": (
                        round(best_sample.rtt_ms, 2)
                        if best_sample is not None
                        else None
                    ),
                }
                wait_until(
                    session.start - DIRECT_START_BEFORE,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    on_wait=lambda remaining: LOGGER.info(
                        "direct HTTP API first batch in %.1f seconds", remaining
                    ),
                )
                state.mark_submit_intent()
                state.mark_api_request_started()
                save_state(state)
                body = build_do_goods_payload(
                    material.offer,
                    page_url=material.page_url,
                )

                result = self._burst(
                    page,
                    material,
                    body,
                    server_base=server_base,
                    monotonic_base=monotonic_base,
                    target=session.start,
                )
                state.mark_api_response_received()
                save_state(state)
                diagnostics["burst"] = result
                successes = int(result.get("successes") or 0)
                if successes:
                    state.mark_order_created()
                    state.mark_payment_stage()
                    save_state(state)
                    return Outcome(
                        OutcomeKind.ORDER_CREATED,
                        f"direct HTTP API returned {successes} order identifier(s)",
                        region=SHANGHAI_REGION_NAME,
                        total=material.offer.total,
                        orders=successes,
                    )
                if (
                    int(result.get("ambiguous") or 0)
                    or int(result.get("fatal") or 0)
                    or int(result.get("network_errors") or 0)
                    or int(result.get("parse_errors") or 0)
                ):
                    return Outcome(
                        OutcomeKind.UNKNOWN,
                        "direct HTTP API produced ambiguous responses "
                        f"(ambiguous={result.get('ambiguous', 0)}, "
                        f"network={result.get('network_errors', 0)})",
                    )
                return Outcome(
                    OutcomeKind.RETRYABLE_FAILURE,
                    f"direct HTTP API completed {result.get('responses', 0)} "
                    "responses without an order",
                )
            except Exception as error:
                LOGGER.exception("direct HTTP API runner failed")
                return Outcome(
                    OutcomeKind.HALTED,
                    f"direct HTTP API failed: {type(error).__name__}",
                )
            finally:
                context.close()
                self._mark_timing(diagnostics, "browser_closed")
                self._save_run_diagnostics(diagnostics)

    def _sign_at(
        self,
        page: Any,
        body: dict[str, object],
        *,
        now_ms: int,
    ) -> tuple[str, str]:
        result = page.evaluate(
            """async ({body, nowTime}) => {
              const runtime = window.__tencentPurchaseRuntime;
              if (!runtime) throw new Error('signing runtime missing');
              const [signature] = await runtime.device.ge(
                [
                  'post', , '/dianshi/do-goods', , {}, ,
                  body, , , navigator.userAgent, runtime.signingSkey
                ],
                nowTime
              );
              return {
                signature: String(signature || ''),
                bodyText: JSON.stringify(body)
              };
            }""",
            {"body": body, "nowTime": now_ms},
        )
        if not isinstance(result, dict):
            raise ValueError("invalid signing response")
        signature = str(result.get("signature") or "")
        body_text = str(result.get("bodyText") or "")
        if not signature or not body_text:
            raise ValueError("empty signature or request body")
        return signature, body_text

    def _sample_clock(self, client: Any) -> ClockSample | None:
        started = time.monotonic()
        try:
            payload = self._direct_json(client, "/dianshi/get-now", {})
        except Exception:
            LOGGER.warning("direct HTTP API clock sample failed", exc_info=True)
            return None
        received = time.monotonic()
        server_now_ms = server_now_from_payload(payload)
        if server_now_ms is None:
            return None
        return ClockSample(
            server_now_ms=server_now_ms,
            rtt_ms=max(0.0, (received - started) * 1000),
            received_monotonic=received,
        )

    def _burst(
        self,
        page: Any,
        material: DirectHttpMaterial,
        body: dict[str, object],
        *,
        server_base: int,
        monotonic_base: float,
        target: dt.datetime,
    ) -> dict[str, object]:
        target_ms = int(target.timestamp() * 1000)
        stop_at_ms = target_ms + int(DIRECT_STOP_AFTER.total_seconds() * 1000)
        groups: Counter[tuple[str, str, str]] = Counter()
        attempts = 0
        responses = 0
        successes = 0
        first_deal_id = ""
        ambiguous = 0
        no_order = 0
        fatal = 0
        network_errors = 0
        parse_errors = 0
        rounds = 0
        batch_sizes: list[int] = []
        stop_dispatch = False

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=DIRECT_BATCH_SIZE
        ) as executor:
            while not stop_dispatch:
                now_ms = int(
                    server_base + (time.monotonic() - monotonic_base) * 1000
                )
                if now_ms >= stop_at_ms or attempts >= DIRECT_MAX_ATTEMPTS:
                    break

                batch_size = min(
                    DIRECT_BATCH_SIZE,
                    DIRECT_MAX_ATTEMPTS - attempts,
                )
                signed: list[tuple[str, str]] = []
                for _ in range(batch_size):
                    now_ms = int(
                        server_base + (time.monotonic() - monotonic_base) * 1000
                    )
                    try:
                        signed.append(
                            self._sign_at(
                                page,
                                body,
                                now_ms=now_ms,
                            )
                        )
                    except Exception:
                        parse_errors += 1
                        groups[("unknown", "SIGN", "signing failed")] += 1
                        stop_dispatch = True
                        break
                if not signed:
                    break
                rounds += 1
                batch_sizes.append(len(signed))
                futures = [
                    executor.submit(
                        self._send_signed,
                        material.client,
                        material,
                        body_text,
                        signature,
                    )
                    for signature, body_text in signed
                ]
                attempts += len(futures)
                done, not_done = concurrent.futures.wait(
                    futures,
                    timeout=DIRECT_REQUEST_TIMEOUT_SECONDS + 0.5,
                )
                late_done: set[concurrent.futures.Future[dict[str, object]]] = set()
                if not_done:
                    late_done, not_done = concurrent.futures.wait(
                        not_done,
                        timeout=DIRECT_REQUEST_TIMEOUT_SECONDS,
                    )
                round_has_408 = False
                for future in done | late_done:
                    try:
                        item = future.result()
                    except Exception:
                        network_errors += 1
                        groups[("network", "THREAD", "request failed")] += 1
                        continue
                    responses += 1
                    disposition = str(item.get("disposition") or "unknown")
                    code = str(item.get("code") or "")
                    message = str(item.get("message") or "")
                    groups[(disposition, code, message)] += 1
                    if code == "408":
                        round_has_408 = True
                    if disposition == ApiDisposition.ORDER_CREATED.value:
                        successes += 1
                        stop_dispatch = True
                        first_deal_id = first_deal_id or str(
                            item.get("deal_id") or ""
                        )
                    elif disposition in {
                        ApiDisposition.NOT_STARTED.value,
                        ApiDisposition.SOLD_OUT.value,
                        ApiDisposition.RATE_LIMITED.value,
                    }:
                        no_order += 1
                    elif disposition in {
                        ApiDisposition.NOT_LOGGED_IN.value,
                        ApiDisposition.CSRF_ERROR.value,
                        ApiDisposition.INELIGIBLE.value,
                    }:
                        fatal += 1
                        stop_dispatch = True
                    else:
                        ambiguous += 1
                        if code in {"1100110", "1101411"}:
                            stop_dispatch = True
                    if str(item.get("error") or "") == "parse":
                        parse_errors += 1
                network_errors += len(not_done)
                if not_done:
                    groups[("network", "TIMEOUT", "request in flight")] += len(
                        not_done
                    )
                if stop_dispatch:
                    break
                gap = (
                    DIRECT_408_BACKOFF_SECONDS
                    if round_has_408
                    else random.uniform(
                        DIRECT_BATCH_GAP_MIN_SECONDS,
                        DIRECT_BATCH_GAP_MAX_SECONDS,
                    )
                )
                time.sleep(gap)

        return {
            "attempts": attempts,
            "responses": responses,
            "rounds": rounds,
            "batch_sizes": batch_sizes,
            "successes": successes,
            "first_deal_id": first_deal_id,
            "ambiguous": ambiguous,
            "no_order": no_order,
            "fatal": fatal,
            "network_errors": network_errors,
            "parse_errors": parse_errors,
            "response_groups": [
                {
                    "kind": kind,
                    "code": code,
                    "message": message,
                    "count": count,
                }
                for (kind, code, message), count in groups.items()
            ],
        }

    def _send_signed(
        self,
        client: Any,
        material: DirectHttpMaterial,
        body_text: str,
        signature: str,
    ) -> dict[str, object]:
        headers = {
            str(material.runtime.get("signature_header") or "x-dss"): signature,
        }
        device_header = str(material.runtime.get("device_header") or "")
        device_token = str(material.runtime.get("deviceToken") or "")
        if device_header and device_token:
            headers[device_header] = device_token
        try:
            response = client.post(
                f"{API_HOST}{DO_GOODS_PATH}",
                content=body_text,
                headers=headers,
            )
        except Exception:
            return {"disposition": "network", "code": "", "message": ""}
        try:
            payload = response.json()
        except Exception:
            return {
                "disposition": "unknown",
                "code": str(response.status_code),
                "message": "",
                "error": "parse",
            }
        result = classify_do_goods_response(payload)
        return {
            "disposition": result.disposition.value,
            "code": result.code,
            "message": result.message,
            "deal_id": result.deal_id,
        }

    @staticmethod
    def _direct_json(
        client: Any,
        path: str,
        payload: dict[str, object],
    ) -> object:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        response = client.post(f"{API_HOST}{path}", content=text)
        response.raise_for_status()
        return response.json()
