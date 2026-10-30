"""Playwright browser flow for the confirmed Tencent product."""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import quote

from .checks import (
    card_snapshot_problems,
    classify_cta,
    compact,
    explicit_submit_failure,
    extract_real_total,
    parse_price,
    reached_payment_stage,
    region_candidates,
    validate_order_text,
    validate_selected_region,
    validate_total,
)
from .constants import (
    ACTIVITY_URL,
    PRODUCT_NAME,
    REGION_PRIORITY,
    SUBMIT_RESULT_TIMEOUT_SECONDS,
)
from .outcome import Outcome, OutcomeKind
from .schedule import SaleSession, now_china, wait_until
from .settings import RunnerSettings
from .state import DayState

LOGGER = logging.getLogger(__name__)


class StopFlow(RuntimeError):
    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.reason)
        self.outcome = outcome


def _text(locator: Any, timeout_ms: int = 5_000) -> str:
    return locator.inner_text(timeout=timeout_ms)


def _visible(locator: Any) -> bool:
    try:
        return locator.is_visible()
    except Exception:
        return False


def _enabled(locator: Any) -> bool:
    try:
        if not locator.is_enabled():
            return False
        return locator.evaluate(
            """el => {
              const cls = typeof el.className === 'string' ? el.className : '';
              return !el.disabled
                && el.getAttribute('aria-disabled') !== 'true'
                && !/\\b(is-disabled|disabled)\\b/.test(cls);
            }"""
        )
    except Exception:
        return False


class TencentPurchaseBrowser:
    def __init__(
        self,
        settings: RunnerSettings,
        *,
        clock: Callable[[], dt.datetime] = now_china,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.sleeper = sleeper

    def _playwright(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright is not installed; run the Windows installer first"
            ) from error
        return sync_playwright

    def _launch(self, playwright: Any, *, headless: bool = False) -> Any:
        self.settings.ensure_directories()
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.profile_dir),
                channel="msedge",
                headless=headless,
                viewport={"width": 1366, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=[
                    "--disable-background-mode",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-popup-blocking",
                    "--no-first-run",
                ],
            )
        except Exception as error:
            raise RuntimeError(
                "Could not launch Edge with the dedicated automation profile"
            ) from error
        context.set_default_timeout(5_000)
        context.set_default_navigation_timeout(30_000)
        return context

    def login_interactive(
        self,
        *,
        timeout_seconds: int = 600,
        reset_cookies: bool = False,
    ) -> bool:
        sync_playwright = self._playwright()
        with sync_playwright() as playwright:
            context = self._launch(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                if reset_cookies:
                    removed = self._clear_tencent_cookies(context)
                    print(f"Cleared {removed} Tencent Cloud cookies from this profile.")
                page.goto(
                    "https://cloud.tencent.com/login?s_url="
                    + quote(ACTIVITY_URL, safe=""),
                    wait_until="domcontentloaded",
                )
                print(
                    "Dedicated automation Edge is open on the Tencent Cloud login "
                    f"page. Complete login within {timeout_seconds} seconds."
                )
                deadline = dt.datetime.now(tz=dt.UTC) + dt.timedelta(
                    seconds=timeout_seconds
                )
                verified = False
                while dt.datetime.now(tz=dt.UTC) < deadline:
                    if self._has_login_cookies(context):
                        if not verified:
                            try:
                                page.goto(ACTIVITY_URL, wait_until="domcontentloaded")
                            except Exception:
                                LOGGER.debug(
                                    "activity page navigation after login failed",
                                    exc_info=True,
                                )
                            verified = True
                        if self._activity_login_ok(page, context):
                            persisted = self._persist_tencent_login(context)
                            print("Tencent Cloud activity login verified.")
                            print(
                                f"Persisted {persisted} session cookies "
                                "inside the dedicated Edge profile."
                            )
                            return True
                    self.sleeper(1)
                print("Login was not detected before the timeout.")
                return False
            finally:
                context.close()

    def _clear_tencent_cookies(self, context: Any) -> int:
        cookies = context.cookies()
        preserved: list[dict[str, Any]] = []
        removed = 0
        for cookie in cookies:
            domain = str(cookie.get("domain", ""))
            normalized = domain.lstrip(".").lower()
            if normalized != "tencent.com" and not normalized.endswith(
                ".tencent.com"
            ):
                preserved.append(cookie)
                continue
            removed += 1
        if not removed:
            return 0
        context.clear_cookies()
        if preserved:
            context.add_cookies(preserved)
        return removed

    def reset_login_cookies(self) -> dict[str, int]:
        sync_playwright = self._playwright()
        with sync_playwright() as playwright:
            context = self._launch(playwright)
            try:
                removed = self._clear_tencent_cookies(context)
                remaining = sum(
                    1
                    for cookie in context.cookies()
                    if str(cookie.get("domain", "")).lstrip(".").lower()
                    == "tencent.com"
                    or str(cookie.get("domain", "")).lstrip(".").lower().endswith(
                        ".tencent.com"
                    )
                )
                return {"removed": removed, "remaining_tencent": remaining}
            finally:
                context.close()

    def _persist_tencent_login(self, context: Any) -> int:
        expires = int(time.time()) + 7 * 24 * 60 * 60
        cookies_to_update: list[dict[str, Any]] = []
        for cookie in context.cookies():
            normalized = str(cookie.get("domain", "")).lstrip(".").lower()
            if normalized != "tencent.com" and not normalized.endswith(
                ".tencent.com"
            ):
                continue
            try:
                current_expiry = float(cookie.get("expires") or -1)
            except (TypeError, ValueError):
                current_expiry = -1
            if current_expiry > time.time():
                continue
            updated: dict[str, Any] = {
                key: cookie[key]
                for key in (
                    "name",
                    "value",
                    "domain",
                    "path",
                    "httpOnly",
                    "secure",
                    "sameSite",
                )
                if key in cookie
            }
            updated["expires"] = expires
            cookies_to_update.append(updated)
        if cookies_to_update:
            context.add_cookies(cookies_to_update)
        return len(cookies_to_update)

    def _activity_login_ok(self, page: Any, context: Any) -> bool:
        cookies = {
            str(cookie.get("name", "")).lower(): str(cookie.get("value", ""))
            for cookie in context.cookies()
            if cookie.get("value")
        }
        skey = cookies.get("skey") or cookies.get("p_skey")
        if skey:
            token = 5381
            for character in skey:
                token += (token << 5) + ord(character)
            csrf = str(token & 0x7FFFFFFF)
        else:
            csrf = cookies.get("qcmaincsrftoken", "")
            if not csrf:
                return False
        try:
            result = page.evaluate(
                """async csrf => {
                  const response = await fetch(
                    'https://act-api.cloud.tencent.com/dianshi/check-login',
                    {
                      method: 'POST',
                      credentials: 'include',
                      cache: 'no-store',
                      headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRF-TOKEN': csrf
                      },
                      body: '{}'
                    }
                  );
                  return await response.json();
                }""",
                csrf,
            )
        except Exception:
            LOGGER.debug("activity login verification request failed", exc_info=True)
            return False
        return isinstance(result, dict) and result.get("code") in (0, "0")

    def inspect(self) -> dict[str, object]:
        sync_playwright = self._playwright()
        with sync_playwright() as playwright:
            context = self._launch(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._close_extra_pages(context, page)
                self._load_activity(page)
                logged_in = self._wait_for_login_state(page, context)
                result: dict[str, object] = {
                    "url": page.url,
                    "logged_in": logged_in,
                }
                if logged_in:
                    result["diagnostics"] = self._sale_dom_diagnostics(page)
                    try:
                        card = self._target_card(page)
                    except StopFlow as stopped:
                        result["card_error"] = stopped.outcome.reason
                    else:
                        result["card"] = self._wait_for_card_ready(card)
                return result
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
                wait_until(
                    session.browser_ready_at,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    on_wait=lambda remaining: LOGGER.info(
                        "waiting %.1f seconds to prepare the browser", remaining
                    ),
                )
                self._load_activity(page)
                self._mark_timing(diagnostics, "activity_loaded")
                if not self._wait_for_login_state(page, context):
                    return Outcome(
                        OutcomeKind.HALTED,
                        "Tencent Cloud login is missing; run the login setup command",
                    )
                remaining = max(
                    15.0,
                    (session.poll_end - self.clock()).total_seconds(),
                )
                card = self._target_card(page, timeout_seconds=remaining)
                self._wait_for_card_ready(card)
                self._mark_timing(diagnostics, "target_ready")
                active_snapshot, active_target_token = self._wait_for_active_cta(
                    page,
                    session,
                )
                self._mark_timing(diagnostics, "active_cta_observed")
                problems = card_snapshot_problems(active_snapshot)
                if problems:
                    raise StopFlow(Outcome(OutcomeKind.HALTED, "; ".join(problems)))
                self._click_active_cta(page, active_target_token)
                self._mark_timing(diagnostics, "cta_click_returned")
                modal = self._wait_for_config_dialog(page)
                self._mark_timing(diagnostics, "config_dialog_visible")
                diagnostics["config_before"] = self._capture_controls_safely(
                    modal,
                    "config_before",
                )
                region, purchase_button = self._configure_product(page, modal)
                self._mark_timing(diagnostics, "config_selected")
                diagnostics["config_after"] = self._capture_controls_safely(
                    modal,
                    "config_after",
                )
                order_page = self._continue_to_order_check(page, purchase_button)
                self._mark_timing(diagnostics, "order_page_visible")
                return self._submit_once(
                    order_page,
                    state,
                    region,
                    save_state,
                    diagnostics,
                )
            except StopFlow as stopped:
                return stopped.outcome
            finally:
                context.close()
                self._mark_timing(diagnostics, "browser_closed")
                self._save_run_diagnostics(diagnostics)

    def _close_extra_pages(self, context: Any, keep: Any) -> None:
        for page in list(context.pages):
            if page != keep:
                try:
                    page.close()
                except Exception:
                    LOGGER.debug("could not close an extra startup page", exc_info=True)

    def _load_activity(self, page: Any) -> None:
        for attempt in range(2):
            try:
                page.goto(ACTIVITY_URL, wait_until="domcontentloaded")
                break
            except Exception as error:
                if attempt == 1:
                    raise
                LOGGER.warning(
                    "activity navigation failed once (%s); retrying once",
                    type(error).__name__,
                )
                self.sleeper(0.5)
        if "/login" in page.url:
            return
        ms = page.locator("#MS")
        ms.wait_for(state="attached", timeout=15_000)
        ms.scroll_into_view_if_needed()
        page.wait_for_timeout(1_500)

    def _is_logged_in(self, page: Any) -> bool:
        if "/login" in page.url:
            return False
        nav = page.locator("#navigationBar")
        if nav.count() == 0:
            return False
        return bool(
            nav.evaluate(
                """root => {
                  const visible = el => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width > 0
                      && rect.height > 0;
                  };
                  const label = el => (el.innerText || el.textContent || '')
                    .replace(/\\s+/g, '')
                    .trim();
                  const links = [...root.querySelectorAll('a,button')]
                    .filter(visible);
                  if (links.length < 3) return false;
                  return !links.some(el => ['登录', '免费注册'].includes(label(el)));
                }"""
            )
        )

    def _has_login_cookies(self, context: Any) -> bool:
        # Presence only: cookie names are inspected locally and values are never logged.
        try:
            cookies = context.cookies(["https://cloud.tencent.com"])
        except Exception:
            return False
        login_names = {
            "skey",
            "p_skey",
        }
        return any(
            str(cookie.get("name", "")).lower() in login_names
            and bool(cookie.get("value"))
            for cookie in cookies
        )

    def _wait_for_login_state(
        self,
        page: Any,
        context: Any,
        *,
        timeout_seconds: float = 12,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._is_logged_in(page) and self._has_login_cookies(context):
                return True
            self.sleeper(0.5)
        return False

    def _target_card(self, page: Any, *, timeout_seconds: float = 15) -> Any:
        root = page.locator("#MS")
        started = time.monotonic()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            cards = root.locator(".uno3-buy-card-seckill")
            matches: list[Any] = []
            for index in range(cards.count()):
                card = cards.nth(index)
                if not _visible(card):
                    continue
                card_text = _text(card, timeout_ms=1_500)
                if PRODUCT_NAME in card_text and compact("4核4G3M") in compact(card_text):
                    matches.append(card)
            if len(matches) == 1:
                LOGGER.info(
                    "target card found after %.2f seconds",
                    time.monotonic() - started,
                )
                return matches[0]
            if len(matches) > 1:
                raise StopFlow(
                    Outcome(
                        OutcomeKind.HALTED,
                        "more than one visible target seckill card matched",
                    )
                )
            self.sleeper(0.5)
        self._save_evidence(page, "target-missing")
        self._save_dom_snapshot(page, "target-missing")
        raise StopFlow(
            Outcome(
                OutcomeKind.RETRYABLE_FAILURE,
                "the exact target seckill card was not found",
            )
        )

    def _sale_dom_diagnostics(self, page: Any) -> dict[str, object]:
        root = page.locator("#MS")
        if root.count() == 0:
            return {"root": False}
        return root.evaluate(
            """root => {
              const compact = value => (value || '').replace(/\\s+/g, ' ').trim();
              const visible = el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const cards = [...root.querySelectorAll('.uno3-buy-card')];
              return {
                root: true,
                text: compact(root.innerText).slice(0, 1800),
                skeletons: root.querySelectorAll('.uno3-skeleton').length,
                cardCount: cards.length,
                cards: cards.map((card, index) => ({
                  index,
                  visible: visible(card),
                  classes: card.className,
                  text: compact(card.innerText).slice(0, 500),
                  cta: compact(card.querySelector('.uno3-buy-card__btn')?.innerText)
                }))
              };
            }"""
        )

    def _card_snapshot(self, card: Any) -> dict[str, object]:
        return card.evaluate(
            """el => {
              const cta = el.querySelector('.uno3-buy-card__btn');
              const progress = el.querySelector('.uno3-buy-card__progress-bar-inner');
              const wrapper = cta && cta.closest('.uno3-buy-card__btn-progress');
              const text = value => (value || '').replace(/\\s+/g, ' ').trim();
              return {
                text: text(el.innerText),
                cta: text(cta && cta.innerText),
                ctaTag: cta ? cta.tagName.toLowerCase() : '',
                disabled: !!(cta && cta.disabled)
                  || !!(wrapper && wrapper.classList.contains('is-disabled')),
                progress: progress ? progress.style.width : '',
                classes: el.className
              };
            }"""
        )

    def _wait_for_card_ready(self, card: Any) -> dict[str, object]:
        deadline = time.monotonic() + 8
        latest: dict[str, object] = {}
        while time.monotonic() < deadline:
            latest = self._card_snapshot(card)
            problems = card_snapshot_problems(latest)
            if not problems:
                return latest
            if "活动价" in str(latest.get("text", "")) and "价格查询中" not in str(
                latest.get("text", "")
            ):
                break
            self.sleeper(0.5)
        problems = card_snapshot_problems(latest)
        raise StopFlow(Outcome(OutcomeKind.HALTED, "; ".join(problems)))

    def _wait_for_active_cta(
        self,
        page: Any,
        session: SaleSession,
    ) -> tuple[dict[str, object], str]:
        wait_until(
            session.poll_start,
            clock=self.clock,
            sleeper=self.sleeper,
            on_wait=lambda remaining: LOGGER.info(
                "waiting %.1f seconds before the five-second status window", remaining
            ),
        )
        if not self._wait_for_login_state(page, page.context, timeout_seconds=4):
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, "the browser session expired at the sale boundary")
            )
        if self.clock() > session.poll_end:
            raise StopFlow(
                Outcome(OutcomeKind.RETRYABLE_FAILURE, "the sale window already expired")
            )
        timeout_ms = max(
            100,
            int((session.poll_end - self.clock()).total_seconds() * 1000),
        )
        target_token = f"tp-active-{time.monotonic_ns()}"
        result = page.evaluate(
            """({timeoutMs, targetToken}) => new Promise(resolve => {
              const compact = value => (value || '').replace(/\\s+/g, '').trim();
              const observedScope = document.querySelector('#MS') || document.documentElement;
              let settled = false;
              let lastSnapshot = {};
              const finish = (state, snapshot = {}) => {
                if (settled) return;
                settled = true;
                observer.disconnect();
                clearTimeout(timer);
                resolve({state, snapshot});
              };
              const inspect = () => {
                const root = document.querySelector('#MS');
                if (!root) return;
                const cards = [...root.querySelectorAll('.uno3-buy-card-seckill')]
                  .filter(card => {
                    const style = getComputedStyle(card);
                    const rect = card.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width > 0
                      && rect.height > 0;
                  });
                const matches = cards.filter(card => {
                  const text = compact(card.innerText);
                  return text.includes('轻量应用服务器') && text.includes('4核4G3M');
                });
                if (matches.length > 1) {
                  finish('multiple');
                  return;
                }
                if (matches.length !== 1) return;
                const card = matches[0];
                const cta = card.querySelector('.uno3-buy-card__btn');
                const progress = card.querySelector('.uno3-buy-card__progress-bar-inner');
                const wrapper = cta && cta.closest('.uno3-buy-card__btn-progress');
                const snapshot = {
                  text: (card.innerText || '').replace(/\\s+/g, ' ').trim(),
                  cta: (cta?.innerText || '').replace(/\\s+/g, ' ').trim(),
                  disabled: !!(cta && cta.disabled)
                    || !!(wrapper && wrapper.classList.contains('is-disabled')),
                  progress: progress ? progress.style.width : ''
                };
                lastSnapshot = snapshot;
                const text = compact(snapshot.text);
                const priceReady = text.includes('活动价') && text.includes('38')
                  && !text.includes('价格查询中');
                if (
                  priceReady
                  && compact(snapshot.cta) === '立即抢购'
                  && !snapshot.disabled
                ) {
                  card.setAttribute('data-tp-active-target', targetToken);
                  finish('active', snapshot);
                } else if (
                  compact(snapshot.cta).includes('已抢光')
                  || compact(snapshot.cta).includes('已结束')
                ) {
                  finish('unavailable', snapshot);
                }
              };
              const observer = new MutationObserver(inspect);
              observer.observe(observedScope, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['class', 'style', 'aria-disabled', 'disabled']
              });
              const timer = setTimeout(
                () => finish('timeout', lastSnapshot),
                Math.max(100, timeoutMs)
              );
              inspect();
            })""",
            {"timeoutMs": timeout_ms, "targetToken": target_token},
        )
        state = str(result.get("state", "timeout"))
        snapshot = result.get("snapshot") or {}
        if state == "active":
            LOGGER.info(
                "event-driven sale trigger cta=%s progress=%s",
                snapshot.get("cta"),
                snapshot.get("progress"),
            )
            return snapshot, target_token
        if state == "unavailable":
            raise StopFlow(
                Outcome(
                    OutcomeKind.RETRYABLE_FAILURE,
                    f"the product became unavailable: {snapshot.get('cta', '')}",
                )
            )
        if state == "multiple":
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, "multiple target cards appeared at the sale boundary")
            )
        self._save_evidence(page, "active-trigger-timeout")
        self._save_dom_snapshot(page, "active-trigger-timeout")
        if page.url.startswith("https://cloud.tencent.com/login"):
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, "the browser session expired during the sale")
            )
        raise StopFlow(
            Outcome(
                OutcomeKind.RETRYABLE_FAILURE,
                "the exact product never showed an enabled 立即抢购 action",
            )
        )

    def _click_active_cta(self, page: Any, target_token: str) -> None:
        target = page.locator(f'[data-tp-active-target="{target_token}"]')
        cta = target.locator(".uno3-buy-card__btn")
        if cta.count() != 1:
            raise StopFlow(Outcome(OutcomeKind.HALTED, "active CTA was not unique"))
        snapshot = self._card_snapshot(target)
        problems = card_snapshot_problems(snapshot)
        state = classify_cta(
            str(snapshot.get("cta", "")),
            bool(snapshot.get("disabled", False)),
        )
        if problems or state != "active":
            raise StopFlow(
                Outcome(
                    OutcomeKind.RETRYABLE_FAILURE,
                    "the target changed state immediately before the CTA click",
                )
            )
        try:
            cta.first.click(timeout=3_000)
        finally:
            try:
                target.evaluate("el => el.removeAttribute('data-tp-active-target')")
            except Exception:
                LOGGER.debug("could not remove temporary active-target marker", exc_info=True)

    def _wait_for_config_dialog(self, page: Any) -> Any:
        token = f"tp-config-{time.monotonic_ns()}"
        result = page.evaluate(
            """({timeoutMs, token}) => new Promise(resolve => {
              const visible = el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const text = el => (el.innerText || el.textContent || '')
                .replace(/\\s+/g, ' ').trim();
              let settled = false;
              const finish = (state, detail = {}) => {
                if (settled) return;
                settled = true;
                observer.disconnect();
                clearTimeout(timer);
                resolve({state, ...detail});
              };
              const inspect = () => {
                const dialogs = [...document.querySelectorAll(
                  '.rno-modal, .tea-dialog, [role="dialog"], .uno3-dialog'
                )].filter(el => visible(el)
                  && text(el).includes('轻量应用服务器')
                  && (text(el).includes('配置') || text(el).includes('镜像')));
                if (dialogs.length > 1) {
                  finish('multiple');
                  return;
                }
                if (dialogs.length === 1) {
                  dialogs[0].setAttribute('data-tp-config-dialog', token);
                  finish('found');
                  return;
                }
                const target = [...document.querySelectorAll(
                  '#MS .uno3-buy-card-seckill'
                )].find(el => {
                  const value = text(el);
                  return value.includes('轻量应用服务器') && value.includes('4核4G3M');
                });
                if (target) {
                  const cta = target.querySelector('.uno3-buy-card__btn');
                  const ctaText = cta ? text(cta) : '';
                  if (ctaText.includes('已抢光') || ctaText.includes('已结束')) {
                    finish('unavailable', {detail: ctaText});
                    return;
                  }
                }
                const alerts = [...document.querySelectorAll(
                  '.tea-alert, .tea-message, .rno-message, .rno-alert, '
                  + '.qc-base-alert, .buy-alert, [role="alert"]'
                )].filter(visible).map(text).filter(Boolean);
                const stockMarkers = ['已售罄', '已抢光', '库存不足'];
                const eligibilityMarkers = [
                  '不满足活动参与条件', '不满足购买条件',
                  '已达到单个用户优惠限额'
                ];
                const stockHit = alerts.find(value =>
                  stockMarkers.some(marker => value.includes(marker)));
                if (stockHit) {
                  finish('unavailable', {detail: stockHit});
                  return;
                }
                const eligibilityHit = alerts.find(value =>
                  eligibilityMarkers.some(marker => value.includes(marker)));
                if (eligibilityHit) {
                  finish('ineligible', {detail: eligibilityHit});
                }
              };
              const observer = new MutationObserver(inspect);
              observer.observe(document.body, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['class', 'style', 'aria-disabled', 'disabled']
              });
              const timer = setTimeout(() => finish('timeout'), timeoutMs);
              inspect();
            })""",
            {"timeoutMs": 12_000, "token": token},
        )
        state = str(result.get("state", "timeout"))
        if state == "found":
            element = page.locator(
                f'[data-tp-config-dialog="{token}"]'
            ).element_handle()
            if element is not None:
                return element
        self._save_evidence(page, f"config-dialog-{state}")
        self._save_dom_snapshot(page, f"config-dialog-{state}")
        if state == "unavailable":
            raise StopFlow(
                Outcome(
                    OutcomeKind.RETRYABLE_FAILURE,
                    f"product became unavailable before configuration: {result.get('detail', '')}",
                )
            )
        if state == "ineligible":
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    f"eligibility check blocked configuration: {result.get('detail', '')}",
                )
            )
        if state == "multiple":
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    "multiple product configuration dialogs matched",
                )
            )
        raise StopFlow(
            Outcome(
                OutcomeKind.HALTED,
                "the product configuration dialog did not appear; evidence captured",
            )
        )

    def _find_form_group(self, modal: Any, labels: tuple[str, ...]) -> Any | None:
        groups = modal.locator(".qc-base-form-item, .uno3-dialog-form-item")
        for index in range(groups.count()):
            group = groups.nth(index)
            if not _visible(group):
                continue
            group_text = _text(group, timeout_ms=1_000)
            if any(label in group_text for label in labels):
                return group
        return None

    def _select_group_option(
        self,
        page: Any,
        modal: Any,
        *,
        labels: tuple[str, ...],
        desired: str,
    ) -> bool:
        group = self._find_form_group(modal, labels)
        if group is None:
            return False
        if self._click_text_when_ready(group, desired, timeout_ms=250):
            if self._wait_for_group_selected(group, desired):
                return True

        trigger = group.locator(
            ".qc-base-select, button, [role='combobox'], .qc-base-select-trigger"
        )
        for index in range(trigger.count()):
            item = trigger.nth(index)
            if _visible(item) and _enabled(item):
                item.click(timeout=3_000)
                if self._click_text_when_ready(modal, desired, timeout_ms=700):
                    if self._wait_for_group_selected(group, desired):
                        return True
                if self._click_text_when_ready(page, desired, timeout_ms=300):
                    if self._wait_for_group_selected(group, desired):
                        return True
                break
        return self._wait_for_group_selected(group, desired, timeout_ms=300)

    def _click_text_when_ready(
        self,
        scope: Any,
        value: str,
        *,
        timeout_ms: int,
    ) -> bool:
        wanted = compact(value)
        token = f"tp-{time.monotonic_ns()}"
        found = scope.evaluate(
            """(root, {wanted, token, timeoutMs}) => new Promise(resolve => {
              const compact = value => (value || '').replace(/\\s+/g, '').trim();
              const visible = el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const enabled = el => {
                const cls = typeof el.className === 'string' ? el.className : '';
                return !el.disabled
                  && el.getAttribute('aria-disabled') !== 'true'
                  && !/\\b(is-disabled|disabled)\\b/.test(cls);
              };
              let settled = false;
              const finish = value => {
                if (settled) return;
                settled = true;
                observer.disconnect();
                clearTimeout(timer);
                resolve(value);
              };
              const inspect = () => {
                const candidates = [...root.querySelectorAll(
                  'button, [role="radio"], [role="option"], label, '
                  + '.qc-base-radio, .qc-base-radio-btn, .qc-base-select, '
                  + '.qc-base-select-item, .qc-base-select-item-cont, li'
                )];
                const match = candidates.find(el => visible(el)
                  && enabled(el)
                  && compact(el.innerText || el.textContent) === wanted);
                if (!match) return;
                match.setAttribute('data-tp-click-target', token);
                finish(true);
              };
              const observer = new MutationObserver(inspect);
              observer.observe(root, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['class', 'style', 'aria-disabled', 'disabled']
              });
              const timer = setTimeout(() => finish(false), timeoutMs);
              inspect();
            })""",
            {"wanted": wanted, "token": token, "timeoutMs": timeout_ms},
        )
        if not found:
            return False
        target = scope.locator(f'[data-tp-click-target="{token}"]')
        try:
            target.click(timeout=3_000)
        finally:
            try:
                target.evaluate("el => el.removeAttribute('data-tp-click-target')")
            except Exception:
                LOGGER.debug("could not remove temporary click marker", exc_info=True)
        return True

    def _wait_for_group_selected(
        self,
        group: Any,
        desired: str,
        *,
        timeout_ms: int = 800,
    ) -> bool:
        wanted = compact(desired)
        alternatives: list[str] = []
        if wanted in {compact(item) for item in REGION_PRIORITY}:
            alternatives = [compact(item) for item in REGION_PRIORITY if item != desired]
        elif wanted == compact("linux类"):
            alternatives = [compact("windows类")]
        return bool(
            group.evaluate(
                """(root, {wanted, alternatives, timeoutMs}) => new Promise(resolve => {
                  const compact = value => (value || '').replace(/\\s+/g, '').trim();
                  const visible = el => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                      && style.visibility !== 'hidden'
                      && rect.width > 0
                      && rect.height > 0;
                  };
                  let settled = false;
                  const finish = value => {
                    if (settled) return;
                    settled = true;
                    observer.disconnect();
                    cancelAnimationFrame(frame);
                    clearTimeout(timer);
                    resolve(value);
                  };
                  const inspect = () => {
                    const markers = [...root.querySelectorAll(
                      '[aria-checked="true"], [aria-selected="true"], .is-active, '
                      + '.is-selected, .qc-base-radio-btn-checked, '
                      + '.qc-base-select-item-selected'
                    )].filter(visible);
                    if (markers.some(el => compact(el.innerText || el.textContent)
                      .includes(wanted))) {
                      finish(true);
                      return;
                    }
                    const text = compact(root.innerText);
                    if (text.includes(wanted)
                      && !alternatives.some(value => text.includes(value))) {
                      finish(true);
                    }
                  };
                  const observer = new MutationObserver(inspect);
                  observer.observe(root, {
                    subtree: true,
                    childList: true,
                    characterData: true,
                    attributes: true,
                    attributeFilter: ['class', 'style', 'aria-checked', 'aria-selected']
                  });
                  let frame = requestAnimationFrame(function tick() {
                    inspect();
                    if (!settled) frame = requestAnimationFrame(tick);
                  });
                  const timer = setTimeout(() => finish(false), timeoutMs);
                  inspect();
                })""",
                {
                    "wanted": wanted,
                    "alternatives": alternatives,
                    "timeoutMs": timeout_ms,
                },
            )
        )

    def _configure_product(self, page: Any, modal: Any) -> tuple[str, Any]:
        region = ""
        default_region = self._selected_region(modal)
        for candidate in region_candidates(default_region):
            if candidate == default_region:
                region = candidate
                break
            if self._select_group_option(
                page,
                modal,
                labels=("地域",),
                desired=candidate,
            ):
                region = candidate
                break
        if not validate_selected_region(region):
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, "no allowed region could be selected")
            )

        image_group = self._find_form_group(modal, ("镜像",))
        if image_group is not None:
            linux_selected = self._select_group_option(
                page,
                modal,
                labels=("镜像",),
                desired="linux类",
            )
        else:
            modal_text = compact(_text(modal, timeout_ms=2_000))
            linux_selected = (
                "linux类" in modal_text
                and "windows类" not in modal_text
            )
        if not linux_selected:
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, "Linux image could not be verified")
            )

        modal_text = compact(_text(modal, timeout_ms=2_000))
        required = ("1年", "数量", "38")
        if any(item not in modal_text for item in required):
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    "configuration dialog did not show one year, quantity and the expected price",
                )
            )
        self._ensure_agreement(modal)
        buttons = modal.locator("button")
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if not _visible(button):
                continue
            label = compact(_text(button, timeout_ms=1_000))
            if label in {"立即抢购", "立即购买", "确认购买"} and _enabled(button):
                return region, button
        raise StopFlow(
            Outcome(OutcomeKind.HALTED, "the configuration dialog had no enabled purchase button")
        )

    def _selected_region(self, modal: Any) -> str:
        group = self._find_form_group(modal, ("地域",))
        if group is None:
            return ""
        markers = group.locator(
            "[aria-checked='true'], [aria-selected='true'], .is-active, "
            ".is-selected, .qc-base-radio-btn-checked, "
            ".qc-base-select-item-selected"
        )
        for index in range(markers.count()):
            marker = markers.nth(index)
            if not _visible(marker):
                continue
            marker_text = compact(_text(marker, timeout_ms=1_000))
            if "售罄" in marker_text:
                continue
            for region in REGION_PRIORITY:
                if compact(region) in marker_text:
                    return region

        group_text = compact(_text(group, timeout_ms=2_000))
        if "售罄" in group_text:
            return ""
        present = [
            region for region in REGION_PRIORITY if compact(region) in group_text
        ]
        if len(present) == 1:
            return present[0]
        return ""

    def _ensure_agreement(self, root: Any) -> None:
        labels = root.locator("label")
        for index in range(labels.count()):
            label = labels.nth(index)
            if not _visible(label):
                continue
            text = compact(_text(label, timeout_ms=1_000))
            if "我已阅读并同意" not in text or "腾讯云服务协议" not in text:
                continue
            checkbox = label.locator("input[type='checkbox']")
            if checkbox.count() and not checkbox.first.is_checked():
                label.click(timeout=3_000)
            return

    def _continue_to_order_check(self, page: Any, purchase_button: Any) -> Any:
        try:
            with page.context.expect_page(timeout=15_000) as popup_info:
                purchase_button.click(timeout=3_000)
            order_page = popup_info.value
            order_page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception as error:
            if "buy.cloud.tencent.com/order/check" not in page.url:
                raise StopFlow(
                    Outcome(
                        OutcomeKind.HALTED,
                        "the order confirmation page did not open in a new tab",
                    )
                ) from error
            order_page = page
        if "buy.cloud.tencent.com/order/check" not in order_page.url:
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    f"unexpected order confirmation URL: {order_page.url}",
                )
            )
        if order_page != page:
            try:
                page.close()
            except Exception:
                LOGGER.debug("activity page close failed", exc_info=True)
        return order_page

    def _submit_once(
        self,
        page: Any,
        state: DayState,
        region: str,
        save_state: Callable[[DayState], None],
        diagnostics: dict[str, object],
    ) -> Outcome:
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
        body = _text(page.locator("body"), timeout_ms=10_000)
        if "页面出现了错误" in body or "订单参数有误" in body:
            raise StopFlow(
                Outcome(OutcomeKind.HALTED, "the confirmed order page reported an error")
            )
        problems = validate_order_text(body, region=region)
        if problems:
            raise StopFlow(Outcome(OutcomeKind.HALTED, "; ".join(problems)))

        total_locator = page.locator(".submit-info .price")
        total_text = _text(total_locator.first, timeout_ms=5_000) if total_locator.count() else ""
        total = parse_price(total_text) if total_text else extract_real_total(body)
        if not validate_total(total):
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    f"confirmation total did not equal 38.00 yuan: {total_text or total}",
                )
            )

        submit = page.locator("button.submit-btn")
        if submit.count() != 1 or not _visible(submit):
            raise StopFlow(Outcome(OutcomeKind.HALTED, "unique payment-stage button not found"))
        submit_text = compact(_text(submit))
        if submit_text in {"确认支付", "下一步"}:
            state.mark_payment_stage()
            save_state(state)
            raise StopFlow(
                Outcome(
                    OutcomeKind.ORDER_CREATED,
                    "the browser was already on the payment stage; no payment was clicked",
                    region=region,
                    total=total,
                )
            )
        if submit_text not in {"去支付", "提交订单"}:
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    f"unexpected order-generation button text: {submit_text}",
                )
            )

        blocked = self._visible_order_blocker(page)
        if blocked:
            kind, reason = blocked
            raise StopFlow(Outcome(kind, f"pre-submit order block: {reason}"))
        self._ensure_agreement(page)
        if not _enabled(submit):
            raise StopFlow(
                Outcome(
                    OutcomeKind.HALTED,
                    "the order-generation button stayed disabled after agreement checks",
                )
            )
        diagnostics["order_before_submit"] = self._capture_controls_safely(
            page,
            "order_before_submit",
        )
        self._mark_timing(diagnostics, "order_ready_to_submit")
        state.mark_submit_intent()
        save_state(state)
        try:
            submit.click(timeout=5_000)
        except Exception:
            state.mark_click_returned()
            save_state(state)
            diagnostics["order_after_submit"] = self._capture_controls_safely(
                page,
                "order_after_submit",
            )
            self._save_evidence(page, "submit-dispatch-error", region=region, total=total)
            return Outcome(
                OutcomeKind.UNKNOWN,
                "clicking the order-generation button ended ambiguously",
                region=region,
                total=total,
            )
        state.mark_click_returned()
        save_state(state)
        self._mark_timing(diagnostics, "submit_click_returned")

        deadline_ms = SUBMIT_RESULT_TIMEOUT_SECONDS * 1000
        try:
            page.wait_for_function(
                """() => {
                  const title = document.title || '';
                  const body = document.body?.innerText || '';
                  if (title.includes('支付订单-腾讯云')
                    || body.includes('订单提交成功，待支付')) {
                    return 'payment';
                  }
                  const failureMarkers = [
                    '订单提交失败', '下单失败', '生成订单失败',
                    '商品已售罄', '已抢光', '不满足购买条件',
                    '已达到单个用户优惠限额'
                  ];
                  return failureMarkers.some(marker => body.includes(marker))
                    ? 'failure' : false;
                }""",
                timeout=deadline_ms,
                polling="raf",
            )
        except Exception:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                pass

        try:
            current_text = _text(page.locator("body"), timeout_ms=3_000)
            page_title = page.title()
        except Exception:
            current_text = ""
            page_title = ""
        if reached_payment_stage(page_title, current_text):
            self._mark_timing(diagnostics, "payment_stage_observed")
            state.mark_payment_stage()
            save_state(state)
            diagnostics["order_after_submit"] = self._capture_controls_safely(
                page,
                "order_after_submit",
            )
            self._save_evidence(page, "unpaid-order", region=region, total=total)
            return Outcome(
                OutcomeKind.ORDER_CREATED,
                "unpaid order reached the payment stage",
                region=region,
                total=total,
            )
        if explicit_submit_failure(current_text):
            self._mark_timing(diagnostics, "submit_failure_observed")
            diagnostics["order_after_submit"] = self._capture_controls_safely(
                page,
                "order_after_submit",
            )
            self._save_evidence(page, "submit-failure", region=region, total=total)
            return Outcome(
                OutcomeKind.RETRYABLE_FAILURE,
                "Tencent reported a conclusive order generation failure",
                region=region,
                total=total,
            )
        diagnostics["order_after_submit"] = self._capture_controls_safely(
            page,
            "order_after_submit",
        )
        self._mark_timing(diagnostics, "submit_unknown_observed")
        self._save_evidence(page, "submit-unknown", region=region, total=total)
        return Outcome(
            OutcomeKind.UNKNOWN,
            "order generation result was ambiguous; no retry was attempted",
            region=region,
            total=total,
        )

    def _visible_order_blocker(
        self,
        page: Any,
    ) -> tuple[OutcomeKind, str] | None:
        result = page.evaluate(
            """() => {
              const visible = el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const text = el => (el.innerText || el.textContent || '')
                .replace(/\\s+/g, ' ').trim();
              const candidates = [...document.querySelectorAll(
                '.tea-alert, .tea-message, .rno-message, .rno-alert, '
                + '.qc-base-alert, .buy-alert, .order-prompt, '
                + '.buy-descriptions-item, [role="alert"]'
              )].filter(visible).map(text).filter(Boolean);
              return candidates.slice(0, 80);
            }"""
        )
        joined = "\n".join(str(item) for item in result)
        hard_markers = (
            "不满足活动参与条件",
            "不满足购买条件",
            "已达到单个用户优惠限额",
        )
        stock_markers = ("商品已售罄", "当前商品已售罄", "库存不足", "已抢光")
        for marker in hard_markers:
            if marker in joined:
                return OutcomeKind.HALTED, marker
        for marker in stock_markers:
            if marker in joined:
                return OutcomeKind.RETRYABLE_FAILURE, marker
        return None

    def _capture_controls(self, scope: Any) -> dict[str, object]:
        return scope.evaluate(
            """root => {
              const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
              const visible = el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0
                  && rect.height > 0;
              };
              const nodes = [...root.querySelectorAll(
                '.qc-base-form-item, label, input, select, button, '
                + '[role="radio"], [role="checkbox"], [aria-checked]'
              )].filter(visible);
              return {
                text: clean(root.innerText).slice(0, 6000),
                controls: nodes.slice(0, 200).map((el, index) => ({
                  index,
                  tag: el.tagName.toLowerCase(),
                  type: el.getAttribute('type') || '',
                  role: el.getAttribute('role') || '',
                  name: el.getAttribute('name') || '',
                  classes: typeof el.className === 'string' ? el.className : '',
                  text: clean(el.innerText || el.textContent).slice(0, 400),
                  checked: 'checked' in el ? !!el.checked : null,
                  disabled: 'disabled' in el ? !!el.disabled : null,
                  ariaChecked: el.getAttribute('aria-checked'),
                  ariaDisabled: el.getAttribute('aria-disabled')
                }))
              };
            }"""
        )

    def _capture_controls_safely(self, scope: Any, label: str) -> dict[str, object]:
        try:
            return self._capture_controls(scope)
        except Exception as error:
            LOGGER.warning("could not capture controls for %s: %s", label, error)
            return {"error": type(error).__name__, "label": label}

    def _save_run_diagnostics(self, diagnostics: dict[str, object]) -> None:
        if len(diagnostics) <= 2:
            return
        self.settings.ensure_directories()
        stamp = self.clock().strftime("%Y%m%d-%H%M%S")
        path = self.settings.evidence_dir / f"{stamp}-purchase-flow.json"
        diagnostics.pop("_started_monotonic", None)
        path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _mark_timing(self, diagnostics: dict[str, object], label: str) -> None:
        started = diagnostics.get("_started_monotonic")
        if not isinstance(started, (int, float)):
            return
        timings = diagnostics.setdefault("timing_ms", {})
        if isinstance(timings, dict):
            timings[label] = round((time.monotonic() - started) * 1000, 2)

    def _save_evidence(
        self,
        page: Any,
        label: str,
        *,
        region: str = "",
        total: Decimal | None = None,
    ) -> None:
        self.settings.ensure_directories()
        stamp = self.clock().strftime("%Y%m%d-%H%M%S")
        path = self.settings.evidence_dir / f"{stamp}-{label}.png"
        try:
            page.screenshot(path=str(path), full_page=True, timeout=10_000)
        except Exception:
            LOGGER.exception("could not capture evidence screenshot %s", path)
        sidecar = path.with_suffix(".json")
        sidecar.write_text(
            json.dumps(
                {
                    "label": label,
                    "url": page.url,
                    "title": page.title(),
                    "region": region,
                    "total": str(total or ""),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _save_dom_snapshot(self, page: Any, label: str) -> None:
        stamp = self.clock().strftime("%Y%m%d-%H%M%S")
        path = self.settings.evidence_dir / f"{stamp}-{label}-dom.json"
        payload = self._sale_dom_diagnostics(page)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def run_browser(
    settings: RunnerSettings,
    session: SaleSession,
    state: DayState,
    *,
    save_state: Callable[[DayState], None],
) -> Outcome:
    return TencentPurchaseBrowser(settings).run(session, state, save_state=save_state)
