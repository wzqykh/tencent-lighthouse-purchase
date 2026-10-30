# Tencent Lighthouse purchase runner

This repository code is intended only for a user's own account and personal
purchase. It must not be used for bulk purchasing, account rental, proxy-pool
evasion, credential sharing or commercial proxy ordering. The user is
responsible for complying with the activity rules and Tencent Cloud terms.
Internal activity endpoints and response codes are not stable public APIs and
may change without notice.

This standalone runner uses Playwright with a dedicated Edge profile to create
unpaid orders for the confirmed Tencent Cloud `4核4G3M` Lighthouse deal during
a bounded sale window. The scheduled `http-api` path uses Edge only to load the
authenticated session, keep cookies in process memory and run Tencent's current
signing function; order requests go through an independent Python HTTP/2
client. It never pays. A bounded attempt window may create multiple unpaid
orders; the user accepts manual cleanup.

## Safety boundaries

- Dedicated Edge profile under `%LOCALAPPDATA%\TencentPurchaseRunner\edge-profile`.
- One browser context, one active page and one OS execution lock.
- The only submission region is Shanghai, `regionId=4`.
- The current activity schedule must return the exact target goods, Linux image,
  one-year duration, quantity one and a 38-yuan real cost.
- Request headers and signing functions come from the loaded Tencent page. Cookie
  and token values are never written to disk, exported in output or logged.
- The planned direct-submission window is 14:59:58.5 through a hard stop at
  15:00:30.
- At about T-20 seconds, make one lightweight `get-now` request to establish
  the DNS/TCP/TLS/HTTP2 connection.
- At about T-10 seconds, make two `get-now` samples and use the lowest-RTT
  sample to calibrate server time. Do not make network requests again until
  the first order batch.
- Send batches of at most 10 concurrent requests.
- Wait for each batch, then wait 350–600ms with jitter before the next batch.
- After any 408, wait 1.5 seconds before the next batch.
- A hard total-attempt cap of 200 must always apply.
- Stop adding submissions immediately when an order identifier is returned.
- The runner never clicks a payment control. The user pays manually on a phone.

The activity API is not a documented stable public contract. A page deployment
can change its payload, signing modules or response shape. The runner fails
closed when any part cannot be verified and does not fall back to third-party
copies of old requests.

## Windows setup

Run the scripts from an interactive PowerShell session:

```powershell
.\install.ps1
.\configure-secret.ps1
.\setup-login.ps1
.\http-preflight.ps1
.\notify-test.ps1
.\install-task.ps1
```

`configure-secret.ps1` encrypts the PushPlus token with Windows DPAPI for the
current user. The token is never accepted as a command-line argument and is not
written to project memory or logs.

`setup-login.ps1` opens the dedicated Edge profile. Sign in with the identity
associated with the intended Tencent Cloud account. If Tencent opens an account
association page, associate the existing cloud account rather than creating a
new one.

The activity login is verified against `check-login`. After success, session
cookies needed for the next unattended run are made persistent for seven days
inside the dedicated profile only. Values are never printed or exported.
Cookie expiry is not a guarantee of server-side session lifetime; always run
`preflight` again after a long idle period.

`http-preflight.ps1` verifies direct HTTP/2 login, exact offer, 38-yuan price,
Shanghai configuration and page signing runtime without calling `do-goods`.
`http-probe.ps1` sends one signed request for diagnostics and can still create
an unpaid order if stock is available.

`install-task.ps1` registers the daily 09:55 and 14:55 tasks, keeps the machine
awake on AC power, and disables lid-close sleep on AC power. The display may turn
off and the desktop may stay locked.

## Runtime data

All runtime data remains outside the repository:

```text
%LOCALAPPDATA%\TencentPurchaseRunner\
  edge-profile\
  evidence\
  logs\
  state\
  pushplus.dpapi
```

Screenshots, diagnostics and logs may contain order details but never exported
browser cookies. Review evidence on the local machine and delete it after the
order is resolved.

## Failure behavior

- Missing activity login material, failed `check-login`, CAPTCHA/risk prompt,
  product mismatch or price mismatch stops the current day's remaining sessions
  before a signed order request is sent.
- A conclusive “not started” or “sold out” response creates no order and may be
  retried only inside the bounded window and attempt cap.
- Login, CSRF, CAPTCHA and non-retryable eligibility errors halt the runner.
- A `408` response is recorded, followed by the configured short cooldown, and
  may continue until the hard deadline or attempt cap.
- Transient network failures may retry within the configured bound. Never
  continue adding submissions after an order identifier is observed.
- `code=0` is not sufficient by itself. An order is marked created only when the
  response includes a `big_deal_no`, `deal_id` or `dealId`.

`click_returned_at` and the old UI methods remain for compatibility with earlier
state files. The scheduled runner now uses `direct_api.py`.
