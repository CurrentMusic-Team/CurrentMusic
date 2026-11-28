# 哔哩哔哩 API

从 B 站视频分 P 播放独立音轨或观看 MV。首页平台选择器选「哔哩哔哩」，可按关键词、BV 号或视频页链接搜索。视频详情可切换分 P。

## 接口

外接客户端使用 `POST /cm/music-service/v1/{operation}`，请求体为 `{"provider":"bilibili","input":{...}}`。Web 使用 `POST /cm/bilibili/v1/{operation}`，请求体直接传 `input`。`GET /cm/bilibili/capabilities` 返回实例的 `catalog` 和 `playback` 开关。搜索和详情可匿名使用，取流需要 CurrentMusic 登录会话及播放权限，外接客户端还需 `music.play`。

| 操作 | 输入与结果 |
| --- | --- |
| `catalog.search` | `query`、`kind`（`song`/`mv`）、`offset`、`limit`。关键词结果为各视频的首页分 P。BV 或链接可返回全部分 P。返回 `items`、`nextOffset` |
| `catalog.track` / `catalog.video` | `id` 为 `BV号:CID`。返回 `resource` 和 `parts`。`uploader` 是投稿人，时长用 `durationMs` |
| `playback.qualities` | 同一 `id`，返回可用的 `dolby`、`standard` 档位 |
| `playback.resolve` | `id`、`quality`（`auto`、`dolby`、`standard`）、`supportsDolby`。返回 `url`、`format`（`m4a`）、实际 `quality`、`codecs`、`bitrate`、`trackId`、`expiresIn` 和来源标题。自动档在客户端支持时优先使用 `30250` 全景声 |
| `playback.mv` | 传 `id`，或传 `linkedTrack` 对象，例如 `{"provider":"netease","id":"123"}`。可传 `supportsDolbyVision: false` 选择普通视频和 `supportsDolby: true` 选择适用的全景声音轨。响应含 `url`、`format: "dash"`、`dolbyVision`、`videoCodecs`、`audioCodec`、`expiresIn` 和原视频入口 |

媒体地址支持 `GET`、`HEAD`、单段 `Range`。音频票据和 MV 的 DASH 清单在到期后需重新解析。杜比视界源使用视频清晰度 `126`。Web 按客户端解码能力在 B 站 MV 内回退到普通画质，保留同分 P 的音画对应关系。

## 歌曲关联

网易云和酷狗的自动播放在支持杜比时先尝试原平台杜比，再尝试关联的 B 站杜比，最后继续原平台已有回退。外接原平台的 `playback.resolve` 传 `quality: "auto"` 与 `supportsDolby: true` 参与自动选择。MV 由首页当前选择的平台或 API 的 `provider` 决定。

自动匹配检查标题和单个分 P 的时长，允许与原曲相差最多 **5.000 秒**。合格候选优先选同时有全景声和杜比视界的版本，其次是有全景声，再其次是有杜比视界。结果可能是 Remix、翻唱或 Live，客户端显示视频原标题。管理员可在「哔哩哔哩」中预览候选、指定某个分 P、禁用单首匹配、恢复自动匹配或关闭全站自动匹配。

## 接口清单

<!-- music-service-operations -->

## 服务方配置

在 `/opt/bilibili-api` 将 TZFC fork 固定安装到独立 Python 环境，另安装 `httpx==0.29.0`。本次验证固定到提交 `c58cfdc2d521ad35dcee522e0f8ed30c75d32a26`。保存 `tools/bilibili-api/server.py` 为 `/opt/bilibili-api/server.py`，安装 `deploy/bilibili-api.service`。使用该环境的 Python 运行 `tools/configure-bilibili-cookie.py`，在终端扫码并确认。工具验证大会员后以 `0600` 保存会话。配置完成或会话轮换后重启 `bilibili-api`，以重新加载 systemd credential。适配服务只监听回环地址，公开 HTTP 访问由 CurrentMusic 提供。

CurrentMusic 环境中设置 `CM_BILIBILI_BASE=http://127.0.0.1:3202` 和 `CM_BILIBILI_PLAYBACK=1`。Nginx 通过 `/cm/bilibili/media/` 流式转发，禁用缓存和访问日志。Web 只在播放 B 站 MV 时加载随前端部署的 dash.js。[dash.js 许可](/app/dashjs-LICENSE.txt)
