"""Command-line entry point for setup, inspection and scheduled runs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

from .browser import TencentPurchaseBrowser
from .schedule import current_session, fixed_session, next_session, now_china
from .settings import RunnerSettings


def configure_logging(settings: RunnerSettings) -> None:
    settings.ensure_directories()
    path = settings.log_dir / "runner.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="open the dedicated profile for initial login")
    login.add_argument("--timeout", type=int, default=600)
    login.add_argument(
        "--reset-cookies",
        action="store_true",
        help="clear only Tencent Cloud cookies before opening the login page",
    )
    sub.add_parser(
        "reset-login-cookies",
        help="clear only Tencent Cloud cookies and exit",
    )
    sub.add_parser(
        "restock",
        help="monitor current 15:00 inventory and try to catch returned stock",
    )
    sub.add_parser("inspect", help="read the current target-card state without clicking")
    preflight = sub.add_parser(
        "preflight",
        help="verify login, offer, price and signing without submitting",
    )
    preflight.add_argument("--session", choices=("10:00", "15:00"))
    sub.add_parser(
        "http-preflight",
        help="verify the independent HTTP/2 API path without submitting",
    )
    sub.add_parser(
        "http-probe",
        help="send one signed direct HTTP order request without changing state",
    )
    sub.add_parser(
        "http-api",
        help="run the current sale session through the independent HTTP/2 API",
    )
    sub.add_parser("notify-test", help="send one PushPlus test notification")
    run = sub.add_parser("run", help="run one confirmed sale session")
    run.add_argument("--session", choices=("10:00", "15:00"))
    run.add_argument(
        "--now",
        help="test-only ISO timestamp used to select a session instead of the clock",
    )
    sub.add_parser("scheduled", help="select the task window from the current time")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = RunnerSettings.from_env()
    configure_logging(settings)
    browser = TencentPurchaseBrowser(settings)

    if args.command == "login":
        return (
            0
            if browser.login_interactive(
                timeout_seconds=args.timeout,
                reset_cookies=args.reset_cookies,
            )
            else 1
        )
    if args.command == "reset-login-cookies":
        print(json.dumps(browser.reset_login_cookies(), ensure_ascii=False))
        return 0
    if args.command == "inspect":
        print(json.dumps(browser.inspect(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "preflight":
        from .direct_api import TencentDirectApiBrowser

        session = next_session(now_china(), session_id=args.session)
        result = TencentDirectApiBrowser(settings).preflight(session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        runtime = result.get("runtime")
        return (
            0
            if result.get("logged_in")
            and isinstance(runtime, dict)
            and runtime.get("ready")
            and not result.get("error")
            else 1
        )
    if args.command == "notify-test":
        from .notify import NotificationError, PushPlusNotifier

        try:
            PushPlusNotifier(settings.pushplus_token).send(
                "[腾讯云抢购] 通知通道测试",
                "PushPlus 抢购通知通道可用。此消息不代表已创建订单。",
            )
        except NotificationError:
            logging.getLogger(__name__).exception("PushPlus test notification failed")
            return 1
        print("PushPlus test notification sent.")
        return 0
    if args.command == "http-preflight":
        from .http_api import TencentHttpApiBrowser

        session = next_session(now_china())
        result = TencentHttpApiBrowser(settings).preflight(session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("logged_in") else 1
    if args.command == "http-probe":
        from .http_api import TencentHttpApiBrowser

        session = next_session(now_china())
        result = TencentHttpApiBrowser(settings).probe_do_goods(session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        disposition = result.get("result", {}).get("disposition")
        return 0 if disposition != "network" else 1

    try:
        if args.command in {"scheduled", "http-api"}:
            session = current_session(now_china())
        elif args.command == "restock":
            session = fixed_session(now_china(), "15:00")
        else:
            value = (
                dt.datetime.fromisoformat(args.now)
                if args.now
                else now_china()
            )
            session = fixed_session(value, args.session or "10:00")
    except Exception as error:
        logging.getLogger(__name__).error("cannot select sale session: %s", error)
        return 2

    if args.command == "restock":
        from .notify import NotificationError, PushPlusNotifier
        from .restock import TencentRestockBrowser
        from .runner import notification_for
        from .state import ExecutionLock

        session = fixed_session(now_china(), "15:00")
        lock = ExecutionLock(settings.state_dir / "runner.lock")
        if not lock.acquire():
            logging.getLogger(__name__).warning(
                "another purchase execution is already running"
            )
            return 1
        try:
            outcome = TencentRestockBrowser(settings).watch(session)
            title, body = notification_for(session, outcome)
            try:
                PushPlusNotifier(settings.pushplus_token).send(title, body)
            except NotificationError:
                logging.getLogger(__name__).exception(
                    "restock notification failed"
                )
            print(json.dumps(outcome.__dict__, ensure_ascii=False, default=str))
            return {
                "order_created": 0,
                "retryable_failure": 2,
                "unknown": 3,
                "halted": 7,
            }[outcome.kind.value]
        finally:
            lock.release()

    from .runner import PurchaseRunner

    if args.command == "http-api":
        from .http_api import TencentHttpApiBrowser

        outcome = PurchaseRunner(
            settings,
            runner=lambda settings, session, state, save_state: (
                TencentHttpApiBrowser(settings).run(
                    session,
                    state,
                    save_state=save_state,
                )
            ),
        ).run(session)
    else:
        outcome = PurchaseRunner(settings).run(session)
    if outcome is None:
        return 0
    print(json.dumps(outcome.__dict__, ensure_ascii=False, default=str))
    return {
        "order_created": 0,
        "retryable_failure": 2,
        "unknown": 3,
        "halted": 1,
    }[outcome.kind.value]


if __name__ == "__main__":
    raise SystemExit(main())
