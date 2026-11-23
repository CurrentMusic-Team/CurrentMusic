# 外接音乐服务协议 v1

流声引擎提供独立的声明式服务描述和音乐操作 API。客户端可以实现同一协议并连接不同服务，安装包只需要通用协议客户端。流声引擎在自己的 [连接页](/cm/music-service/connect) 提供入口。

## 发现和连接

| 入口 | 用途 |
| --- | --- |
| `GET /cm/music-service/descriptor` | 读取提供方、API 基址、平台、能力和授权配置 |
| `GET /cm/music-service/connect` | 服务方提供的网页连接入口 |
| `POST /cm/music-service/v1/{operation}` | 执行描述中声明的音乐操作 |

描述是公开 JSON。字段如下。

| 字段 | 要求 |
| --- | --- |
| `format` / `version` | `currentmusic.music-service` / `1` |
| `id` / `name` | 服务标识、供用户确认的显示名 |
| `apiBase` | 操作基址，和描述地址同 HTTPS 源 |
| `platforms` | 平台名到能力数组的映射。每项含 `id`、`label`、`scope`、`write` |
| `authorization` | 可选，提供 `issuer`、`clientId`、`authorizeUrl`、`tokenUrl` 和 `revokeUrl` |

在服务网页的连接按钮中，将无凭据的描述 URL 编码为 `descriptor` 查询参数，链接至流声音乐的 `https://currentmusicx.bileizhen.top/connect`。App 显示 API 域名并等待用户确认。服务方可以更新能力，用户可从原服务网页重新连接。更换服务身份、运营域名、API 基址或授权端点时，应由用户新建连接。

## 操作和数据

外接客户端在请求体传入 `provider` 和 `input`。写入操作在用户确认后另外传 `"confirm": true`。资源 ID 为字符串，时长、播放进度和歌词时间使用毫秒。客户端以连接、平台和资源 ID 区分歌曲。

搜索结果与列表返回 `items` 和下一页的 `nextOffset`，没有更多时为 `null`。分页入参为 `offset` 和 `limit`，最多 50 项。每条资源有 `id`、`kind` 和 `name`，可带 `artists`、`album`、`artwork`、`durationMs`、`videoId`。详情操作返回 `resource`，容器可带 `tracks`、`albums` 和 `nextOffset`。

## 网易云音乐

网易云的外接调用、绑定和代理权限记录在独立的平台文档中。[网易云音乐 API](netease-music-api.md)

## 酷狗概念版

酷狗的外接调用、Web 接口、个人账号和播放配置记录在独立的平台文档中。[酷狗概念版 API](kugou-concept-api.md)

QQ 音乐的公开曲库、歌词和登录规则见接入指南。[QQ 音乐接入](qq-music-api.md)

[机器可读能力目录](/api-docs/music-service-capabilities.json)列出了每项的支持平台、权限及部署开关。[OpenAPI](/api-docs/music-service.openapi.json)描述传输接口。

## 用户授权

沿用[通行证](passport-api.md)的系统浏览器授权码流程。新音乐权限由服务管理员给客户端登记。原生客户端使用 `state` 和 S256 PKCE，回跳校验 `iss`、`state` 与精确登记的 HTTPS 回调地址。客户端通过通行证 token URL 交换授权码，以 Authorization Bearer 请求授权操作。令牌和回调码不进入服务描述、媒体请求或日志。断开连接时使用 revoke URL，并删除本机授权。

外接接口返回 `Cache-Control: no-store`。申请站内登录会话、落雪令牌或应用级授权都无法替代用户的音乐权限。公共操作无需令牌。服务端按能力检查用户范围。错误响应包含 `code` 和 `error`，包括 `authorization_required`、`invalid_token`、`insufficient_scope`、`binding_required`、`playback_unavailable` 和 `unsupported_operation`。

## 服务方部署

使用服务账号运行 `python3 tools/register-music-service-client.py` 可登记 X 的公开客户端。自建实例可设 `CM_MUSIC_SERVICE_CLIENT_ID` 选择另一条已登记的公开客户端。描述会据可用注册和媒体权限列出能力。
