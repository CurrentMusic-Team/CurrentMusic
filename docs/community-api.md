# 个人主页、横幅与帖子

本文路径以 `/cm` 开头。写接口携带 `Authorization: Bearer YOUR_CURRENTMUSIC_TOKEN`，有 JSON 请求体时使用 `Content-Type: application/json`。

## 主页横幅

`GET /cm/auth/me`、`GET /cm/profile` 返回当前账号的 `bannerUrl`。`GET /cm/users/{id}/profile` 可匿名读取，横幅字段位于 `user` 对象。`bannerUrl` 为空串表示未设置。视频横幅同时返回 `bannerType: "video"`，图片横幅省略此字段。将非空路径接在当前 API 基地址（包含 `/cm`）后，例如 `https://music.20110208.xyz/cm/users/42/banner?v=...`。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| PUT | `/cm/profile/banner` | 上传或替换横幅。图片使用 JSON `{"data":"BASE64_IMAGE"}`，视频使用原始 MP4 或 MOV 文件。返回 `bannerUrl`，视频另返回 `bannerType` |
| DELETE | `/cm/profile/banner` | 清除当前账号的横幅。重复删除仍返回 `{"bannerUrl":""}` |
| GET | `/cm/users/{id}/banner` | 公开读取当前横幅，返回 `image/jpeg` 或 `video/mp4`。可用 `v` 指定版本，并使用 ETag 验证。视频支持单段字节范围请求 |
| HEAD | `/cm/users/{id}/banner` | 查询当前横幅的响应头，包括 `Content-Type`、`Content-Length` 和 `ETag`。可用 `v` 指定版本 |

- 图片接受 JPEG、PNG、WebP，解码后最多 1 MiB、2500 万像素。服务端按 EXIF 校正方向，居中裁剪为 3∶2 并转成 JPEG，最长边最多 1600 像素。Web 上传时可拖动和缩放调整取景，再提交已裁好的 3∶2 图片。旧图片仍可读取。
- 视频使用 `Content-Type: video/mp4` 或 `video/quicktime` 并提供 `Content-Length`。文件最多 16 MiB，视频轨最长 5 秒，最长边最多 3840 像素，总像素数最多 8,294,400。服务端校正旋转方向并居中裁剪，输出 720×480 的无声 H.264 MP4。Web 静音循环播放。

上传 MP4 示例。请先将自己的 CurrentMusic 会话令牌存入环境变量 `CURRENTMUSIC_TOKEN`。

```bash
curl --request PUT 'https://music.20110208.xyz/cm/profile/banner' \
  --header "Authorization: Bearer ${CURRENTMUSIC_TOKEN}" \
  --header 'Content-Type: video/mp4' \
  --data-binary '@banner.mp4'
```

```json
{"bannerUrl":"/users/42/banner?v=0123456789abcdef01234567","bannerType":"video"}
```

读取时可用 `If-None-Match` 发送上次取得的 `ETag`，版本相同时返回 `304`。视频请求可使用单段 `Range`（如 `bytes=0-1023`），范围可满足时返回 `206` 和 `Content-Range`，超出文件范围时返回 `416`。

不符合格式、时长或尺寸限制的文件返回 400。请求媒体类型不受支持时返回 415，视频超过大小限制时返回 413，媒体处理组件不可用时返回 503。读取缺失、已移除或已替换的版本返回 404。用户横幅会公开展示。

## 发布与浏览帖子

`POST /cm/posts` 需要登录。标题选填，为修剪首尾空白后最多 80 字的单行字符串。正文必填，修剪后最多 5000 字，以 Markdown 原文保存。音乐附件最多 5 项，类型为 `song`、`album`、`artist` 或 `playlist`，每项至少包含 `type` 与 `id`。

```json
{
  "title": "夜间通勤歌单",
  "content": "分享一首最近反复播放的歌。",
  "attachments": [{"type":"song","id":123,"name":"示例歌曲"}]
}
```

发布成功返回 `{"id":123}`。旧客户端可省略标题，旧帖子与无标题帖子在读取时的 `title` 为 `""`。

`GET /cm/posts?offset=0&limit=20&author=42` 按帖子 ID 降序返回 `{"posts":[...],"total":1}`。`author` 选填，`offset` 默认 0，`limit` 默认为 20、范围为 1 至 50。`GET /cm/posts/{id}` 返回 `{"post":{...}}`，帖子不存在返回 404。两个接口使用同一种帖子结构。

```json
{
  "post": {
    "id": 123,
    "title": "夜间通勤歌单",
    "content": "分享一首最近反复播放的歌。",
    "attachments": [{"type":"song","id":123,"name":"示例歌曲"}],
    "createdAt": 1794960000,
    "author": {
      "id":42,"nickname":"听众","avatar":"","avatarDecoration":"",
      "isAdmin":false,"isSuper":false,"isOwner":false,"title":""
    },
    "likeCount": 2,
    "commentCount": 1,
    "likedByMe": false,
    "canDelete": false
  }
}
```

列表和详情均支持匿名读取，携带当前会话可得到个人的 `likedByMe` 和 `canDelete`。`post.title` 是帖子标题，`post.author.title` 是作者的站内头衔。`POST /cm/posts/{id}/like` 以 JSON `{"on":true}` 设置点赞状态，返回当前 `likeCount`。`DELETE /cm/posts/{id}` 仅允许作者或管理员调用。

## 评论和回复

- `GET /cm/posts/{id}/comments` 可匿名读评论。省略分页参数时返回 `{"comments":[...]}`，最多 100 条。
- `GET /cm/posts/{id}/comments?after=0&limit=30` 按评论 ID 升序返回 `{"comments":[...],"nextAfter":123}`。用上次的 `nextAfter` 请求下一页，`null` 表示读到末尾。`after` 默认 0，`limit` 默认 30，范围为 1 至 100。分页参数无效返回 400。
- `POST /cm/posts/{id}/comments` 需要登录，请求为 `{"content":"评论正文","replyTo":123}`，返回 `{"id":124}`。正文最多 500 字。`replyTo` 可省略，存在时必须指向同一帖子内的评论。
- 每条评论包含 `id`、`postId`、`content`、`createdAt`、`author`、`replyTo`。`replyTo` 包含目标的 `id`、`userId`、`nickname`，普通评论和目标已删除的回复返回 `null`。`DELETE /cm/posts/comments/{id}` 允许评论作者或管理员删除评论。

Web 草稿保存在当前设备，并按 API 地址和账号隔离。发布成功或手动丢弃时清除对应草稿。
