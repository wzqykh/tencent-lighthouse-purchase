# Tencent Lighthouse Purchase Runner

A guarded Windows-local runner for the Tencent Cloud Lighthouse
`4C4G3M` promotional offer. It uses a dedicated Edge profile for login and
request signing, then sends signed `do-goods` requests through a persistent
Python HTTP/2 client.

This project is intended only for a user's own account and personal purchase.
It does not automate payment and must not be used for bulk purchasing, account
rental, proxy-pool evasion, credential sharing or commercial proxy ordering.
Internal activity endpoints and response codes are not stable public APIs and
may change without notice.

## What it does

- Keeps one dedicated browser profile and one process lock.
- Verifies the exact activity, session, Shanghai region, Linux image,
  one-year duration, quantity and CNY 38 price.
- Warms the HTTP/2 connection before the sale.
- Calibrates Tencent server time using the lowest-RTT `get-now` sample.
- Sends a short sequence of bounded concurrent `do-goods` batches.
- Stops on success, pending-order signals, authentication errors or the hard
  deadline.
- Never exports cookies and never clicks or calls a payment action.

Read [tools/tencent_purchase/README.md](tools/tencent_purchase/README.md) for
installation and operating instructions.

The design and live-run history are in
[docs/TENCENT_PURCHASE_SOLUTION.md](docs/TENCENT_PURCHASE_SOLUTION.md).
Rate-limit research and public implementation review are under
[`research/`](research/).

## Windows quick start

From `tools/tencent_purchase/windows`:

```powershell
.\install.ps1
.\configure-secret.ps1
.\setup-login.ps1
.\http-preflight.ps1
.\notify-test.ps1
.\install-task.ps1
```

Runtime data is stored outside the repository under
`%LOCALAPPDATA%\TencentPurchaseRunner`.

## Verification

```bash
python -m unittest discover -s tools/tencent_purchase/tests -t .
```

The checked-in tests cover request construction, scheduling, state transitions,
preflight checks, response classification, notifications and bounded batch
execution.

## Disclaimer

Tencent Cloud's activity rules and platform terms take precedence. The user is
responsible for confirming account eligibility and complying with current
rules. Automation can fail or be rate-limited, and this project does not
guarantee inventory, order creation or purchase success.
