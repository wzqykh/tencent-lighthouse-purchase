"""Run coordination, durable outcomes and PushPlus notifications."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

from .direct_api import run_api_browser
from .notify import NotificationError, PushPlusNotifier
from .outcome import Outcome, OutcomeKind
from .schedule import SaleSession
from .settings import RunnerSettings
from .state import ExecutionLock, StateStore

LOGGER = logging.getLogger(__name__)


def notification_for(session: SaleSession, outcome: Outcome) -> tuple[str, str]:
    total = f"{outcome.total:.2f}" if outcome.total is not None else "未确认"
    region = outcome.region or "未确认"
    if outcome.kind is OutcomeKind.ORDER_CREATED:
        order_count = max(1, outcome.orders)
        title = (
            "[腾讯云抢购] 可能已创建多笔未支付订单"
            if order_count > 1
            else "[腾讯云抢购] 已创建未支付订单"
        )
        cleanup = (
            f"接口返回 {order_count} 个订单标识。请逐笔核对，只支付需要的一笔并取消多余订单。"
            if order_count > 1
            else "请确认只有一笔目标订单。"
        )
        return (
            title,
            "\n".join(
                (
                    "腾讯云下单接口已返回订单标识，自动化已停止，不会付款。",
                    f"场次：{session.session_id}",
                    f"地域：{region}",
                    f"金额：{total} 元",
                    cleanup,
                    "请立即用手机登录同一腾讯云账号，在“费用中心 → 订单管理”确认订单并完成支付。",
                    "促销订单约 60 分钟后失效；不要重复下单。",
                    "付款后请在轻量应用服务器控制台关闭自动续费（若已开启）。",
                )
            ),
        )
    if outcome.kind is OutcomeKind.UNKNOWN:
        return (
            "[腾讯云抢购] 下单结果未知，请勿重复下单",
            "\n".join(
                (
                    "唯一一次下单请求已经发出，但没有得到可确认的订单结果。",
                    f"场次：{session.session_id}",
                    f"地域：{region}",
                    f"金额：{total} 元",
                    "自动化不会刷新或重试，也不会运行当天另一场。",
                    "请立即用手机在“费用中心 → 订单管理”核对是否已有未支付订单。",
                    "若确认并完成付款，请在轻量应用服务器控制台关闭自动续费（若已开启）。",
                )
            ),
        )
    if outcome.kind is OutcomeKind.RETRYABLE_FAILURE:
        next_step = (
            "本场没有进入订单生成阶段；15:00 任务会再尝试一次。"
            if session.session_id == "10:00"
            else "本场没有进入订单生成阶段；当天已无剩余场次。"
        )
        return (
            "[腾讯云抢购] 本场未创建订单",
            "\n".join(
                (
                    f"场次：{session.session_id}",
                    f"原因：{outcome.reason}",
                    next_step,
                )
            ),
        )
    return (
        "[腾讯云抢购] 任务已安全停止",
        "\n".join(
            (
                f"场次：{session.session_id}",
                f"原因：{outcome.reason}",
                "当天剩余场次不会继续执行。请检查登录态、验证码、风控提示或页面异常。",
            )
        ),
    )


class PurchaseRunner:
    def __init__(
        self,
        settings: RunnerSettings,
        *,
        runner: Callable[..., Outcome] | None = None,
    ) -> None:
        self.settings = settings
        self.state_store = StateStore(settings.state_dir)
        self.lock = ExecutionLock(settings.state_dir / "runner.lock")
        self.notifier = PushPlusNotifier(settings.pushplus_token)
        self.runner = runner or run_api_browser

    def run(self, session: SaleSession) -> Outcome | None:
        self.settings.ensure_directories()
        if not self.lock.acquire():
            LOGGER.warning("purchase runner is already active; this invocation exits")
            return None
        try:
            state = self.state_store.load(session.start.date())
            if not state.can_start(session.session_id):
                LOGGER.info(
                    "session %s is blocked by state terminal=%s attempted=%s",
                    session.session_id,
                    state.terminal_status,
                    state.attempted_sessions,
                )
                return None
            state.mark_session(session.session_id, now=dt.datetime.now(tz=dt.UTC))
            self.state_store.save(state)
            try:
                outcome = self.runner(
                    self.settings,
                    session,
                    state,
                    save_state=self.state_store.save,
                )
            except Exception:
                LOGGER.exception("unexpected purchase runner failure")
                outcome = Outcome(
                    OutcomeKind.HALTED,
                    "unexpected runner failure; inspect the local log and browser evidence",
                )
            terminal = outcome.terminal_status
            if terminal is not None:
                state.terminate(
                    terminal,
                    reason=outcome.reason,
                    region=outcome.region,
                    total=f"{outcome.total:.2f}" if outcome.total is not None else "",
                    now=dt.datetime.now(tz=dt.UTC),
                )
            else:
                state.reason = outcome.reason
                state.updated_at = dt.datetime.now(tz=dt.UTC).isoformat()
            self.state_store.save(state)
            self._notify(session, outcome)
            return outcome
        finally:
            self.lock.release()

    def _notify(self, session: SaleSession, outcome: Outcome) -> None:
        title, body = notification_for(session, outcome)
        try:
            self.notifier.send(title, body)
        except NotificationError:
            LOGGER.exception("PushPlus notification failed")
