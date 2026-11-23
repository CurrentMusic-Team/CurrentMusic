# 网易云音乐 API

外接客户端通过 `POST /cm/music-service/v1/{operation}` 调用本文的操作，传 `provider: "netease"`。可通过描述的 `platforms.netease` 查看服务实例已开放的能力。

[外接音乐服务协议](music-service-api.md) · [服务描述](/cm/music-service/descriptor)

## 快速调用

```bash
curl 'https://music.20110208.xyz/cm/music-service/v1/catalog.search' \
  -H 'Content-Type: application/json' \
  --data '{"provider":"netease","input":{"query":"测试曲库","kind":"song","limit":20}}'
```

使用搜索返回的网易云资源 ID 请求后续详情、歌词和播放。外接接口中的 ID 使用十进制字符串。

## 操作用法

| 能力 | 入参和结果 |
| --- | --- |
| `catalog.search` | `query` 和 `kind`，支持 song、album、artist、playlist |
| `catalog.track/album/artist/playlist` | 对应资源的 `id` |
| `discover.hot/suggest/charts/recommendations` | 联想传 `query`。热搜与联想返回字符串列表。榜单和推荐歌曲返回资源 |
| `track.lyrics` | 歌曲 `id`。返回 `lines`，复用[歌词时间轴](lyrics-api.md) |
| `track.comments` / `track.comment.add` | 歌曲 `id`。读评论可传 `sort`、`page`、`limit`、`cursor`。写评论传 `text` |
| `playback.qualities/resolve/mv/download` | 歌曲或 MV 的 `id`，可选 `quality`。音质接口返回 `levels`。媒体响应给出 HTTPS `url`、`quality`、`format`、`bitrate` 和有效期 `expiresIn`（秒） |
| `library.favorites/favorite.set` | 收藏列表及修改。修改传歌曲 `id` 与 `selected` |
| `library.playlists/playlist` | 个人歌单列表及详情，详情传歌单 `id` |
| `library.playlist.create/edit/delete/tracks` | 创建和重命名传 `name`，可选 `description`。编辑、删除和曲目修改传歌单 `id`。曲目修改传 `trackIds`、`selected` |
| `history.recent/record` | 最近播放与记录。首次传歌曲 `id`，更新时附 `playId` 和累计 `elapsedMs` |
| `roaming.songs`、`library.cloud` | 私人漫游曲目及云盘查看 |

## Web 代理与账号

站内客户端可通过 `/cm/ncm/*` 调用网易云代理，通过 `/cm/ncmbind/*` 管理用户自己的绑定。代理以 CurrentMusic 会话 Bearer 鉴权。T0 通常是公开读取，媒体取址以专用路由规则为准。T1 需要有效的网易云绑定。T2 在绑定之外还要求写入确认。T3 通过项目专用接口处理。

[代理 T0 公开读取](netease-t0.md) · [代理 T1 个人读取](netease-t1.md) · [代理 T2 写入](netease-t2.md) · [代理 T3 权限边界](netease-t3.md)

外接客户端使用通行证用户令牌并申请下表所列的音乐权限。云盘、发表评论、私人漫游和受授权的媒体操作使用相应用户的有效网易云绑定。

[第三方网易云绑定与同步](third-party-ncm-sync.md) · [网易云评论接入](ncm-comments-api.md)

## 接口清单

<!-- music-service-operations -->

## 服务方配置

网易云媒体能力通过 `CM_MUSIC_SERVICE_MEDIA` 按需启用 `lyrics,playback,mv,download,cloud`。下载使用独立的 `music.download` 权限。Web 代理使用当前服务实例的网易云网关权限配置。
