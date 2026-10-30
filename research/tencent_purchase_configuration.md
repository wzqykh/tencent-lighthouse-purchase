# Tencent Lighthouse purchase configuration

Research date: 2026-10-27 (Asia/Shanghai)

## Confirmed: auto-renewal can be changed after purchase

Tencent Cloud's official Lighthouse renewal guide states:

- Automatic renewal is enabled by checking
  “账户余额足够时，设备到期后按月自动续费” when purchasing an instance.
- After the instance is created, the existing auto-renewal setting can be changed.
- The user can enable or disable auto-renewal under the instance details'
  “实例信息” section in the Lighthouse console.
- Auto-renewal can also be managed from the renewal management page.
- When enabled, renewal occurs monthly at expiry if the account balance is
  sufficient.

Sources:

- [续费实例](https://cloud.tencent.com/document/product/1207/47835)
- [购买实例](https://cloud.tencent.com/document/product/1207/44580)
- [修改实例续费标识 API](https://cloud.tencent.com/document/product/1207/55547)

The API documentation also confirms that the renewal flag supports explicit
automatic-renewal and manual-renewal states. This makes post-purchase control a
documented product feature rather than an assumption.

## Confirmed: target product data

The current public activity page is:

[Double 11 2026 activity](https://cloud.tencent.com/act/pro/double11-202611?page=featured-202607)

Its public page data for “日常大促-秒杀-轻量 4核4G3M” shows:

| Field | Value |
| --- | --- |
| Product | 轻量应用服务器 |
| SKU | 4核4G3M |
| Instance specification | `国内[特惠型][cpu4核][内存4G][SSD40GB][峰值带宽3M][流量包300GB]` |
| Image choices | `linux类`, `windows类` |
| Duration choices | `1年` only (`12m`) |
| Region choices | 上海, 北京, 广州 |
| Activity price | 38 CNY |
| Original price | 780 CNY |
| Per-user limit | 1 |
| Default image | Linux (`LINUX_UNIX`) |
| Default region | 上海 (`regionId=4`) |
| Default duration | 12 months |
| Default quantity | 1 |

The current page lists two sale sessions per day:

- 10:00–12:00
- 15:00–18:00

The target card presents “个人专享”, “限1个”, a 0.5 discount label, and the
38-yuan activity price. The public product data also includes server-side
eligibility conditions; eligibility has not been proven because the purchase
chain has not yet reached an order request.

## General purchase fields versus the activity quick-buy dialog

The official general Lighthouse purchase guide lists fields such as application
creation method, region, instance package, server name, login method,
auto-renewal, shared storage, duration, and quantity.

The activity page's quick-buy confirmation dialog has not yet been observed in an
active-sale state. Its exact rendered DOM therefore remains unverified. The
current runner expects the narrower configuration fields implied by the product
data: region, image, duration, quantity and price. It must not assume that the
general purchase page's server-name, password or shared-storage fields appear in
the activity modal.

## Operational implication

Auto-renewal should not block the critical purchase path. If enabled, it can be
disabled from the Lighthouse console after the instance is created. The success
notification should tell the user to verify this setting after payment rather
than claiming that it is already disabled.

## Frontend bundle analysis

The activity page dynamically loads the following public bundles:

- `visual-v3.3a94cf44d04eeb1ac4f6.js`
- `qcact-base-lc.fa63a1d67ce59abbe30f.js`
- `uno-ui-v3.67a6ae36935687ce99ca.js`
- `newCheck.6e726ed6b60c47c2b8fb.js` on the order confirmation page

Confirmed from the public activity bundles:

- The activity purchase modal is a generic `BuyDialog`.
- The product configuration body is rendered through a runtime product schema
  and shared form-item components such as `RegionSelectFormItem`,
  `BlueprintSelectFormItem`, `TimespanUnitSelectFormItem`, and
  `GoodsNumberFormItem`.
- The static bundle therefore reveals the component system and DOM conventions,
  but the exact field list, order, visibility and defaults can depend on the
  runtime schema returned for the current account and product.

Confirmed from the public order-confirmation bundle:

- `autoRenewFlag` occurs in the order-check bundle and is read from
  `checkData.orderData.goodsList[*].goodsDetail.autoRenewFlag`.
- The component does not contain a hard-coded checked value for this field.
- The flag is used to conditionally render an additional component when the
  page is in the payment stage. This is not evidence of a pre-submit checkbox
  or of a particular default value on the activity confirmation page.

Therefore:

- Public source can determine component architecture and likely selectors.
- The exact activity modal DOM must be captured after the target CTA opens its
  authenticated configuration dialog.
- The effective auto-renewal default must be read from the runtime purchase
  response or rendered confirmation/payment controls for this account.

The runner now captures the configuration controls before and after selection
and the confirmation/payment controls around order generation. The capture is
held in memory during the critical path and written only after the browser flow
stops.

## 2026-10-27 direct activity API observations

After the user authorized a direct submission inside the existing authenticated
browser context, the current page produced these public values at runtime:

| Field | Value |
| --- | --- |
| Activity ID | `166111704417621` |
| Target goods ID | `1996855673581677` |
| Product type | `lighthouse_v5` |
| Goods type | `bundle_budget_mc_lg4_01` |
| Linux image ID | `lhbp-eqora508` |
| Duration | `12m` |
| Shanghai region ID | `4` |
| Real cost | `3800` cents |
| Total/list cost | `78000` cents |

The activity page's `query-seckill-by-date` request returns the four current
sessions (10:00 and 15:00 for the requested two days), their session IDs, the
target sale, default order parameters and price data. The runner selects only
the session whose start date and time exactly match the scheduled run.

The current public bundle
[`qcact-base-lc.fa63a1d67ce59abbe30f.js`](https://slscache.tencent-cloud.cn/qcact-ssr-dist/template/qcact-base-lc.fa63a1d67ce59abbe30f.js)
constructs `do-goods` as a signed request. Its request wrapper calls the page
device module to create a dynamic signature header, separate from
`X-CSRF-TOKEN`. The current runtime returned the signature header name
`x-dss`; the value is intentionally not recorded.

A read-only probe on 2026-10-27 reproduced `NOT-LOGINED` while WeChat and QQ
authorization were routed through Tencent Cloud's account-association flow.
Associating the existing cloud account resolved the activity login. The next
browser restart then exposed a second issue: the valid activity session used
session cookies that Playwright did not retain after closing the browser.

The login flow now verifies `check-login` first, then converts only the required
Tencent session cookies to seven-day persistent cookies inside the dedicated
profile. Cookie values are not printed, exported or stored in the repository.
The cross-process preflight passed with `check-login code=0`, the exact
2026-10-28 10:00 session and offer, and both `x-dss` and `X-RCE-TOKEN` signing
support.
