# 腾讯云轻量 4核4G3M 38元/年抢购助手

**Tencent Lighthouse Purchase Runner**

A guarded Windows-local runner for the Tencent Cloud Lighthouse
`4C4G3M` promotional offer. It uses a dedicated Edge profile for login and
request signing, then sends signed `do-goods` requests through a persistent
Python HTTP/2 client.

这是一个运行在 Windows 本机的腾讯云轻量应用服务器抢购工具，面向
“4核4G3M、300GB 月流量、38 元/年”等限时活动。程序使用专用 Edge 配置保持
登录态和请求签名，再通过持久化 Python HTTP/2 客户端分批提交订单请求。

This project is intended only for a user's own account and personal purchase.
It does not automate payment and must not be used for bulk purchasing, account
rental, proxy-pool evasion, credential sharing or commercial proxy ordering.
Internal activity endpoints and response codes are not stable public APIs and
may change without notice.

## 功能 / What it does

- 单账号、单浏览器配置、单进程锁。
- 下单前核对活动、场次、上海地域、Linux 镜像、一年时长、数量和 38 元价格。
- 开卖前预热 HTTP/2 连接，并使用最低 RTT 样本校准腾讯云服务器时间。
- 在开卖前后发送少量有上限的并发订单批次，而不是整点前持续高频轮询。
- 成功、出现未支付订单信号、登录异常或到达硬截止时间后立即停止。
- 不导出 Cookie，不点击支付按钮，不调用支付接口。

安装与运行说明见
[tools/tencent_purchase/README.md](tools/tencent_purchase/README.md)。

完整方案和实盘记录见
[docs/TENCENT_PURCHASE_SOLUTION.md](docs/TENCENT_PURCHASE_SOLUTION.md)。
限流研究和事件经过见 [`research/`](research/)。

## Windows 快速开始 / Quick start

在 `tools/tencent_purchase/windows` 目录运行：

```powershell
.\install.ps1
.\configure-secret.ps1
.\setup-login.ps1
.\http-preflight.ps1
.\notify-test.ps1
.\install-task.ps1
```

运行数据保存在仓库之外：
`%LOCALAPPDATA%\TencentPurchaseRunner`.

## 测试 / Verification

```bash
python -m unittest discover -s tools/tencent_purchase/tests -t .
```

测试覆盖请求构造、场次调度、状态流转、预检、响应分类、通知和有限批次执行。

## 免责声明 / Disclaimer

Tencent Cloud's activity rules and platform terms take precedence. The user is
responsible for confirming account eligibility and complying with current
rules. Automation can fail or be rate-limited, and this project does not
guarantee inventory, order creation or purchase success.

腾讯云活动规则及平台协议优先。使用者需要自行确认账号资格并遵守当期规则。
自动化请求可能失败或被限流，本项目不保证库存、订单创建或购买成功。
