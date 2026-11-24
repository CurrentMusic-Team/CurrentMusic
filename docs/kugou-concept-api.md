# 酷狗概念版 API

外接客户端通过 `POST /cm/music-service/v1/{operation}` 调用本文的操作，传 `provider: "kugou"`。公共搜索、详情、歌词与评论读取无需用户令牌，个人和媒体操作按下方清单授权。服务实例的开放状态可在描述的 `platforms.kugou` 中查看。

[外接音乐服务协议](music-service-api.md) · [服务描述](/cm/music-service/descriptor)

## 快速调用

```bash
curl 'https://music.20110208.xyz/cm/music-service/v1/catalog.search' \
  -H 'Content-Type: application/json' \
  --data '{"provider":"kugou","input":{"query":"周杰伦 晴天","kind":"song","limit":5}}'
```

使用搜索结果原样返回的歌曲 `id` 请求歌词。响应的 `lines` 保留酷狗逐字时间轴及上游提供的译文和罗马音。用同一 ID 请求播放时，使用通行证用户令牌并申请 `music.play`。

## 操作用法

| 能力 | 用法 |
| --- | --- |
| `catalog.search` | `query` 和 `kind`，支持 `song`、`album`、`artist`、`playlist`、`lyric`、`mv`。歌词命中可带 `snippet`，歌曲可包含 `videoId` |
| `catalog.track/album/artist/playlist` | 原样传搜索或发现返回的 `id`，歌单详情也接受榜单 ID。容器详情返回 `tracks`、`albums` 和 `nextOffset`，可用的资源元数据包括 `description`、`trackCount` |
| `discover.hot/suggest/charts/playlists` | 公开热搜、联想、榜单和推荐歌单 |
| `discover.playlist.categories`、`discover.playlist.directory`、`discover.artists` | 歌单分类、歌单广场和歌手目录。传 `category` 或 `group` 进行筛选 |
| `discover.podcasts`、`catalog.podcast` | 发现酷狗有声节目和读取节目专辑。详情返回 `resource`、单集 `tracks` 和 `nextOffset`，单集使用标准酷狗曲目 ID |
| `discover.new.songs`、`track.insights` | 推荐新歌和歌曲资料。资料所列音质为文件档位 |
| `discover.recommendations` | 使用独立曲库会话获取每日推荐歌曲，外接客户端需 `music.library.read` |
| `catalog.video` | 以酷狗 MV 哈希作为 `id`，返回名称、歌手和原始视频 ID |
| `track.lyrics` | 歌曲 `id`。返回逐字 KRC 时间轴，或返回逐行 LRC。酷狗提供的译文与罗马音分别写入同一行的 `trans`、`roma` |
| `track.comments` | 歌曲 `id`。支持 `sort: "hot"` 或 `"recommended"`，按 `page` 和 `limit` 分页 |
| `track.comment.add` | 歌曲 `id`、`text`（最多 300 字）和写入确认。需要本人酷狗绑定和 `music.comment.write` |
| `playback.mv` | 以酷狗 MV 哈希作为 `id`。返回可用的完整 MP4 流媒体地址，需播放权限 |
| `playback.qualities/resolve` | 歌曲 `id`。外接客户端需 `music.play`，并检查站内播放权限 |
| `library.favorites/favorite.status/favorite.set` | 收藏列表包含 `items`、`total` 和 `nextOffset`。状态查询传 `ids` 数组（最多 100 首），返回 `selectedIds`。修改传歌曲 `id` 和布尔值 `selected` |
| `library.cloud` | 查看已绑定酷狗账号的云盘，需 `music.cloud.read`。返回 `items`、`nextOffset`、总数和容量字段。文件 ID 形如 `kgc_123`，匹配曲库的条目可能含 `catalogId` |
| `library.playlists/playlist` | 个人歌单和详情。个人歌单 ID 形如 `kgp_123`，每首歌曲使用曲库返回的原始 `id` |
| `library.playlist.create/edit/delete/tracks` | 新建可带 `description` 及 `trackIds`。改名可同时修改描述。修改曲目传歌单 `id`、`trackIds`（最多 50 首）、`selected` |
| `history.summary` | 返回 CurrentMusic 内的累计听歌时长、近七天时长、今日曲目数和近七天播放榜。旧记录缺少日期时计入累计统计 |
| `history.recent/record` | 最近播放按每首歌最后一次播放倒序。记录首次传歌曲 `id`，后续传同一歌曲 `id`、返回的 `playId` 和累计 `elapsedMs` |
| `roaming.songs` | 漫游歌曲。可选 `seedId` 为起始曲目，或使用 `station` 选择电台。可传 `limit`（最多 30 首）、`excludeIds`（最多 100 首）、`offset`。用返回的 `nextOffset` 获取下一批 |
| `roaming.stations/skip` | 电台清单返回 `items`，每项含 `id`、`name` 和 `category`。隐藏曲目传歌曲 `id`，作用于该 CurrentMusic 账号的酷狗每日推荐和漫游 |

歌曲 ID 由酷狗哈希和专辑音频 ID 组成。榜单与公开歌单使用各自的 ID。以接口返回的原始 ID 调用详情、歌词、评论与播放。

## Web 使用与账号绑定

Web 可在首页的「音乐平台」选择网易云音乐或酷狗概念版。选择按服务器和 CurrentMusic 账号保存在当前浏览器。酷狗版首页、搜索和对应详情通过 `POST /cm/kugou/v1/{operation}` 调用共享操作，JSON 请求体为 `input` 对象本身。每日推荐、播放、个人收藏、歌单、历史和漫游携带 CurrentMusic 登录会话 Bearer。Web 写入在请求体内附 `confirm: true`。`GET /cm/kugou/capabilities` 返回公开的 `catalog` 和 `playback` 开关状态。

收藏、个人歌单、播放历史按 CurrentMusic 账号保存，切换平台时在现有界面显示所选平台的音乐。外接客户端分别申请 `music.library.read`、`music.library.write`、`music.history.read` 和 `music.history.write`。酷狗概念版的服务端播放会话仅用于音频取址。

Web 切到酷狗概念版后，可在「我的」页通过短信绑定个人账号。Web 绑定使用 CurrentMusic 登录会话。运营方启用绑定后提供以下接口。

| 入口 | 用法 |
| --- | --- |
| `GET /cm/kugou/account` | 查询当前站内账号的绑定状态，返回 `enabled` 和 `bound` |
| `POST /cm/kugou/account/code` | 传中国大陆手机号 `mobile`，返回 `ttl` 和 `retryAfter`（秒） |
| `POST /cm/kugou/account/verify` | 同一站内账号传短信 `code` 完成绑定。酷狗登录态由服务端保存 |
| `DELETE /cm/kugou/account` | 解除当前站内账号的酷狗绑定 |

## 媒体地址

酷狗音质使用 `standard`、`exhigh`、`lossless`。`auto` 从无损开始，按可用档位降档；指定档位不可用时返回 `playback_unavailable`。取址返回实际音频格式、码率（bps）和 HTTPS 媒体地址。媒体地址支持普通 `GET`、`HEAD` 与单段 `Range`，到期后通过 `playback.resolve` 刷新。上游拒绝、试听或媒体无法验证时不会签发地址。

## 歌单来源与同步

Web 与 X 通过 CurrentMusic 账号同步流声聚合歌单。已绑定用户默认读取当前平台 App 歌单，可在设置中更改来源。原外接协议的 `library.*` 继续使用各自的旧曲库契约。[聚合歌单接口](aggregate-library-api.md)

## 接口清单

<!-- music-service-operations -->

## Web 功能对照

| 功能 | 酷狗概念版现状 |
| --- | --- |
| 曲库与播放 | 支持搜索、详情、榜单、歌单广场、歌手分类、播放音质、逐字歌词和已发布 MV 播放 |
| 我的曲库 | 聚合歌单与收藏可编辑。已绑定账号可读取酷狗 App 歌单，原生写入由服务端开关控制 |
| 听歌足迹和有声节目 | 可查看流声内记录和近七天播放榜，可浏览酷狗有声节目并播放取得授权的单集 |
| 云盘与评论 | 支持本人云盘列表和歌曲评论读写。云盘上传、匹配、删除、私有文件取址及评论楼层互动待验证 |
| 漫游与发现 | 支持私人 FM、种子漫游和推荐筛选。播客收藏、广播目录、新碟和 MV 广场待验证 |
| 设备与下载 | 独立下载、投屏和一起听需完成媒体权限与设备测试 |
| 账号专题 | 网易云的会员任务、消息、动态、歌词本、音乐资产及运营功能有各自的平台要求 |

## 服务方配置

酷狗使用独立开关 `CM_KUGOU_PLAYBACK`。运营方将概念版会话写入 `CM_KUGOU_COOKIE_FILE` 指定的受保护文件，获授权的用户共用这份服务端会话。客户端只负责站内授权。酷狗仍按其上游的账号、地区及曲目播放权限决定是否返回完整音频。

开启个人云盘和发表评论时，将 `CM_KUGOU_ACCOUNT_DIR` 设置为服务账号拥有的绝对目录路径。目录权限为 `0700`，服务以 `0600` 按 CurrentMusic 用户 ID 保存独立酷狗会话。外接客户端仍使用流声通行证令牌访问云盘和评论接口，个人绑定在服务网页完成。
