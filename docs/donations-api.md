# 官方捐赠与第三方接入

官方捐赠渠道允许已登录用户扫码捐赠后自助提交交易单号，记录进入站长管理台。CurrentMusicX 等第三方客户端可以直接复用这套 API，把捐赠入口嵌进自己的「关于 / 设置」页面，不需要自行收款或保存任何支付信息。

## 接入流程（三步）

推荐与官方 Web 端一致的三步交互；其中第 1、3 步各一次请求，二维码图片可直接展示。

```text
① GET /cm/donation/info      确认账号基本信息（服务端快照，客户端不要自建表单收集）
② GET /cm/donation/qr/{recipientId}/{paymentMethod}
                              用户选择收款对象与支付方式，再扫码支付
③ POST /cm/donation          用户回到应用，填写金额与交易单号提交
```

### 第 1 步：读取引导信息

`GET /cm/donation/info`，需要 `Authorization: Bearer <token>`。

```json
{
  "qrPath": "/donation/qr",
  "recipients": [
    {
      "id": "bileizhen",
      "name": "bileizhen",
      "methods": [
        { "id": "wechat", "name": "微信支付", "qrPath": "/donation/qr/bileizhen/wechat" }
      ]
    },
    {
      "id": "rcst20",
      "name": "ℝ𝕔𝕤𝕥𝟚𝟘",
      "methods": [
        { "id": "wechat", "name": "微信支付", "qrPath": "/donation/qr/rcst20/wechat" },
        { "id": "alipay", "name": "支付宝", "qrPath": "/donation/qr/rcst20/alipay" }
      ]
    }
  ],
  "profile": {
    "username": "demo",
    "nickname": "示例用户",
    "email": "demo@example.com",
    "phone": "",
    "created_at": 1763000000
  },
  "noteMax": 120,
  "txnMax": 64,
  "mine": {
    "items": [
      {
        "id": 1, "amount_cents": 660, "note": "加油", "txn_id": "TX20261113001",
        "created_at": 1763000123, "recipient_id": "rcst20", "payment_method": "alipay"
      }
    ],
    "count": 1, "totalCents": 660
  }
}
```

- `profile` 是服务端从账号记录生成的快照，**客户端只需原样展示确认，不要再向用户索要用户名 / 邮箱 / 手机号**；提交时后端同样以服务端记录为准。
- `recipients` 提供收款对象和各自可用的支付方式。客户端用 `name` 展示，用 `id` 提交。二维码路径相对于包含 `/cm` 的 API 基址。
- `noteMax` / `txnMax` 是备注与交易单号长度上限，可据此设置输入框 `maxlength`。
- `mine` 是本人捐赠历史与累计（`amount_cents` 单位为分），可在入口处展示「已捐赠 N 次 · 累计 ¥X」。
- 本人历史和管理列表的 `recipient_id`、`payment_method` 分别记录收款对象和支付方式，缺失字段值为 `null`。历史自助捐赠的收款对象补为 `bileizhen`，支付方式留空。历史代录的两项均留空。

### 第 2 步：展示捐赠码

用户选择收款对象和支付方式后，直接用图片组件加载该方式的 `qrPath`。例如 `<api-base>/cm/donation/qr/rcst20/wechat`（此处 `<api-base>` 为服务地址）。二维码接口公开，可缓存 24 小时。ℝ𝕔𝕤𝕥𝟚𝟘 的微信收款码返回 `image/png`，其支付宝收款码和 bileizhen 的微信赞赏码返回 `image/jpeg`。组合未知或图片缺失时返回 `404`。

旧版客户端可继续使用顶层 `qrPath`，即 `GET /cm/donation/qr`，返回 bileizhen 的微信赞赏码。
新版客户端连接尚未提供 `recipients` 的服务时，也可使用顶层 `qrPath`，提交时省略收款对象和支付方式字段。

### 第 3 步：提交交易单号

`POST /cm/donation`，需要 Bearer token。

```json
{ "amount": 6.6, "txnId": "TX20261113001", "note": "加油", "recipientId": "rcst20", "paymentMethod": "alipay" }
```

| 字段 | 要求 |
| --- | --- |
| `amount` | 数字，0.01 ~ 100000（元）。这是**用户自报金额**，仅用于展示与核对 |
| `txnId` | 6~64 位数字/字母/`._-`，支付应用账单详情中可查；全站唯一 |
| `note` | 可选，最长 120 字，超长部分服务端截断 |
| `recipientId` | 所选收款对象 ID，来自 `recipients[].id` |
| `paymentMethod` | 实际使用的支付方式 ID，来自所选对象的 `methods[].id` |

新版客户端应成对提交 `recipientId` 和 `paymentMethod`。旧版客户端省略两项时，收款对象记为 `bileizhen`，支付方式留空。

成功返回 `200 {"ok": true, "mine": {...}}`，`mine` 为提交后的最新历史，可直接刷新界面。

### 错误语义

| 状态码 | 含义 | 建议提示 |
| --- | --- | --- |
| 401 | 未登录 / token 失效 | 引导重新登录 |
| 400 | 金额越界、单号格式不符、收款对象或支付方式无效 | 校验金额和单号，再核对所选的收款对象与支付方式 |
| 409 | 交易单号已提交过 | 提醒用户核对单号，不要重复提交 |

## 管理端（仅站长侧）

`GET /cm/admin/donations` 提供搜索、分页、收款对象目录 `recipients` 和按用户聚合的次数 / 累计金额（`sort=recent/amount/user_total/user_count/...`）。每笔记录包含 `recipient_id` 和 `payment_method`。`DELETE /cm/admin/donations/{id}` 用于清理误报。这两个接口需要管理员权限。

### 代录捐赠（超管/站长）

线下捐赠或用户无法自助提交时，站长/超管可代任一用户写入捐赠记录：

```json
POST /cm/admin/donations
{ "username": "demo", "amount": 8.8, "note": "代录捐赠", "recipientId": "bileizhen", "paymentMethod": "wechat" }
```

- 目标用户用 `userId`（数字 ID）或 `username` 二选一；账号快照仍取目标用户的服务端记录。
- `amount` 规则与自助提交一致；`txnId` **可选**，留空自动生成 `ADMIN-` 前缀占位单号；真实单号重复返回 409。
- `recipientId`、`paymentMethod` 均可省略。已知收款对象但支付方式未知时，可只提交 `recipientId`。填写支付方式时，必须同时填写支持该方式的收款对象。
- 代录记录带 `created_by`（操作管理员 uid）与 `created_by_name`，列表中以「代录」标记区分，便于审计。
- 普通管理员调用返回 403。

## 隐私与边界

- 交易单号、自报金额与账号快照仅存于 CurrentMusic 服务端，供站长核对捐赠记录，不会下发给其他用户。
- 捐赠完全自愿，不得与任何功能解锁、等级或权益挂钩；客户端不应把捐赠金额展示给捐赠者以外的用户。
- 官方 Web 端实现见 `web/src/pages/donate.js`，可作为交互与文案参考。
