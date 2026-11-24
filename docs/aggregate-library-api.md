# 流声聚合歌单与多端同步

Web 与 CurrentMusicX 通过同一 CurrentMusic 账号同步曲库。请求使用 CurrentMusic 会话 Bearer，个人接口均返回 `Cache-Control: no-store`。

## 歌单与收藏来源

`GET /cm/library/v2/preferences` 返回 `providers.netease` 和 `providers.kugou`。每项包含 `mode`、当前生效的 `source`、`binding` 和 `writable`。`PUT` 接收 `{"providers":{"kugou":"aggregate"}}`，只更新请求列出的平台。

| `mode` | 行为 |
| --- | --- |
| `auto` | 已绑定时使用当前平台的 App 歌单，未绑定时使用流声聚合歌单 |
| `native` | 使用已绑定的 App 歌单。绑定失效后可重新绑定或改选聚合歌单 |
| `aggregate` | 使用按 CurrentMusic 账号同步的聚合曲库 |

酷狗 App 歌单读取使用每个用户自己的服务端会话。部署端只有在验证实际写入后才通过 `CM_KUGOU_NATIVE_LIBRARY_WRITE=1` 开放写入。`writable` 为 `false` 时可浏览已绑定的酷狗 App 歌单，也可在设置中切换为流声聚合歌单进行编辑。

## 歌单与曲目接口

以下路径都位于 `/cm/library/v2`。带 `{id}` 的路由接受响应中原样返回的歌单 ID，形如 `aggregate:27`、`netease:123`、`kugou:456`。曲目使用对象 `{"provider":"kugou","id":"歌曲哈希-专辑音频ID"}`，网易云 `id` 使用十进制字符串。响应中的曲目包含 `name`、`artists`、`artwork` 和 `durationMs` 等可用资料。

| 方法与路径 | 参数和结果 |
| --- | --- |
| `GET /playlists` | `platform` 和可选的 `source`，返回 `items` 和 `nextOffset` |
| `POST /playlists` | 同一查询条件，JSON `name`、可选 `description`。聚合模式接受最多 50 个初始 `tracks` |
| `GET /playlists/{id}` | 返回 `resource`、`tracks`、`nextOffset` |
| `PUT /playlists/{id}` | 修改 `name`、`description` 或聚合歌单的 `shared` |
| `DELETE /playlists/{id}` | 删除本人歌单 |
| `POST /playlists/{id}/tracks` | 添加 1–50 项 `tracks`，已有曲目维持位置 |
| `DELETE /playlists/{id}/tracks` | 移除 1–50 项 `tracks` |
| `PUT /playlists/{id}/order` | 提交歌单完整的 `tracks` 顺序，每项出现一次 |
| `POST /playlists/import` | 传本人网易云同步快照的 `snapshotId`，生成新的聚合副本 |
| `GET /aliases/{id}` | 将旧 `kgp_…` 歌单链接解析为迁移后的聚合歌单，仅本人可用 |
| `GET /shared/{id}` | 公开读取已启用分享的聚合歌单，路径使用聚合歌单 ID 的数字部分 |

查询 `platform` 为 `netease` 或 `kugou`，`source` 可指定 `native` 或 `aggregate`，省略时采用账号偏好。列表和详情支持 `offset` 与 `limit`，最多 50 条。`nextOffset: null` 表示结束。聚合歌单可包含来自两个平台的曲目，按保存的顺序返回。原生歌单接受本平台曲目。

聚合歌单写入需提交最近读取的整数 `version`。冲突返回 `409 revision_conflict`，客户端刷新后重新提交。异平台曲目写入 App 歌单时返回 `400 wrong_platform`。未绑定或绑定失效时提示管理对应平台账号。读取和修改私有聚合歌单需要所有者权限。

## 红心与收藏

`GET /favorites` 返回 `items` 和 `nextOffset`。`POST /favorites/status` 接受最多 100 项 `tracks`，返回已收藏的 `selected` 引用。`PUT /favorites` 接受 `track` 与布尔值 `selected`。三个接口使用同样的 `platform` 与可选 `source` 查询参数。App 模式写入使用本人绑定，聚合模式写入 CurrentMusic 账号。

以前的站内歌单 ID 和酷狗个人歌单链接继续有效。旧版仅识别网易云数字 ID 的客户端读取兼容视图。原网易云同步快照留作历史副本，聚合曲库长期保留混排顺序。
