# Tencent Cloud seckill repository review

Research date: 2026-10-27 (Asia/Shanghai)

This review does not execute third-party code and does not use any real account
credentials. It separates source-code facts from unverified author claims.

## Summary

All reviewed implementations that claim to be fast enough for the one-second
sale bypass the multi-screen UI and call the activity API directly. Their
approaches conflict with this project's previous “UI-only” boundary. The user
subsequently authorized a narrower direct path: one browser context, one region,
one signed `do-goods`, no retry and no concurrency.

None of these repositories should be copied wholesale. Two reviewed repositories
have no declared license, and the reviewed implementations export cookies,
submit concurrently, retry, or may create multiple unpaid orders.

## `zhouyifan007/tencentyun-snake-up`

Repository:
<https://github.com/zhouyifan007/tencentyun-snake-up>

Reviewed commit:
<https://github.com/zhouyifan007/tencentyun-snake-up/commit/cc265841b61adcbe61734f2aa1d355fb8cda172d>

Verified from source:

- Python `requests` client plus Playwright login helper.
- Exports browser cookies to `cookies.json`.
- Calls `/dianshi/check-available` and `/dianshi/do-goods` directly.
- Uses a manually supplied or updated `x-csrf-token`.
- Calls `buy_now` concurrently for three region IDs with `ThreadPoolExecutor`.
- The checked-in activity and goods IDs target an earlier activity snapshot.
- The request construction does not include the current page's dynamic `x-dss`
  signature.

Repository metadata:

- GitHub reports no declared license.
- Latest reviewed commit message: “修复cookie问题与商品Id错误的问题”.
- Default branch `main`.

Useful ideas: separating availability detection from submission, checking the
API response code, and using a browser to retain login. Not acceptable as-is:
cookie export, hard-coded old IDs, three-region concurrency and no evidence of a
single-order guard.

## `kkkksad/tenxunyun`

Repository:
<https://github.com/kkkksad/tenxunyun>

Reviewed commit:
<https://github.com/kkkksad/tenxunyun/commit/56e606598fbb5873f5c6f401da0354954722cc6f>

Verified from README and source:

- Playwright captures the login cookies into `cookies.json`.
- Derives CSRF from `skey`/`p_skey` using the DJB2 variant used by the page.
- Calls the activity purchase endpoint directly.
- Starts approximately three seconds early.
- Retries for about 60 seconds at roughly 0.5-second intervals.
- Concurrently submits Guangzhou, Shanghai and Beijing.
- The README explicitly warns that multiple regions can create multiple unpaid
  orders.
- The checked-in activity snapshot and request omit the current dynamic request
  signature.

Repository metadata:

- GitHub reports no declared license.
- Default branch `main`.

Useful ideas: reading the page's clock, validating `check-available` quota data
and documenting response categories. Not acceptable as-is: cookie export,
multi-region concurrency, repeated submission and multiple-order risk.

## `ZLin98/tencent-cloud-seckill`

Repository URL:
<https://github.com/ZLin98/tencent-cloud-seckill>

The GitHub API returned HTTP 404 on the research date. The original repository
could not be reviewed directly or cited as reproducible evidence. A similarly
named repository was mentioned in prior research, but it was not treated as the
same project without an authoritative rename or redirect.

No implementation claims from this repository are used in the current runner.

## Other candidate

`KenSporger/tencent-cloud-38-seckill` appeared in GitHub search results as an MIT
project aimed at the same 38-yuan product. Its repository tree could not be
retrieved before the request timed out on the research date. It remains
unreviewed and no code or claims from it are used.

## Adopted design ideas

- Keep Playwright and the authenticated Edge profile.
- Verify the current activity schedule instead of trusting hard-coded IDs.
- Reuse the current page's CSRF and dynamic signing runtime.
- Persist submission intent before the request.
- Treat a non-order response as a definitive session failure only when the API
  gives a known no-order reason.
- Treat every unknown result as terminal for the day.

## Explicitly rejected behavior

- Cookie export.
- Multiple region submissions or any concurrency.
- Retry loops after submission.
- Pre-sale submission that relies on repeated “not started” responses.
- Treating `code=0` alone as proof that an unpaid order exists.
- Copying code from repositories without a compatible license.
