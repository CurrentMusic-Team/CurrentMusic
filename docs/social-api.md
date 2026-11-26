# 社交对话、好友与群组

「发现 → 社交」使用流声音乐账号。以下接口均以 `/cm` 为基址，除世界聊天的公开读取外，使用 `Authorization: Bearer <会话令牌>`。写入请求使用 JSON。

「世界聊天」的对话 ID 为 `1`，初始简介为「流声音乐官方公共聊天室」。现有与新注册账号默认加入，访客可阅读。禁言暂停发言和图片上传。被移出的账号保留公开阅读权限，接受群管理者发出的恢复邀请后可再次参与。世界聊天的消息、图片和实时流沿用[公共聊天室接口](chat-api.md)。

## 对话和消息

| 方法 | 路径 | 请求和响应 |
| --- | --- | --- |
| GET | `/social/conversations` | 返回 `{conversations,userId,pending}`。世界聊天置顶，其余按最新消息排序。访客只返回世界聊天，`userId` 为 0。 |
| POST | `/social/conversations` | `{kind:"direct",userId}` 打开好友私聊，双方共享同一对话。已有对话可在删除好友后查看。用 `{kind:"group",name,description?}` 新建私有群。返回 `{conversation}`。 |
| GET | `/social/conversations/{id}` | 返回 `{conversation}`。世界聊天允许访客读取。 |
| PUT | `/social/conversations/{id}` | 群管理者更新 `{description?,announcement?}`，空字符串可清空。简介最多 300 字，公告最多 1,000 字。返回 `{conversation}`。 |
| DELETE | `/social/conversations/{id}` | 成员退出自建群，群主不使用此操作。返回 `{ok:true}`。 |
| GET | `/social/conversations/{id}/messages` | 私聊及私有群历史。可用 `before` 和 `limit`（1–50，默认 30）。返回升序 `{messages,latestSeq}`。 |
| POST | `/social/conversations/{id}/messages` | 发送私有消息，使用公共聊天室的消息格式。可传 `clientId` 供发送重试去重。好友关系解除后暂停发送。 |
| DELETE | `/social/conversations/{id}/messages/{messageId}` | 删除自己发出的消息，群管理者也可删除所在群的消息。返回 `{ok:true}`。 |
| GET | `/social/conversations/{id}/events` | 传 `since` 轮询私有对话，返回 `{events,latestSeq,cursor}`。每页最多 100 条，通过 `cursor` 继续。收到 `reset:true` 时重新同步历史。 |
| POST | `/social/conversations/{id}/images` | 上传 `{file}`，其中 `file` 是最多 1.5 MB 的 JPEG、PNG、GIF 或 WebP 图片的 Base64，返回 `{image}`。上传者在 24 小时内可将图片用于同一对话。 |
| GET | `/social/conversations/{id}/images/{filename}` | 读取私有图片。发出前仅上传者可预览，发出后仅有该对话访问权的用户可读。 |
| PUT | `/social/conversations/{id}/read` | 传 `{messageId}` 更新当前用户的已读位置，位置只向前推进。返回 `{ok:true}`。 |

对话对象包含 `id`、`kind`（`world`、`group`、`direct`）、`name`、`description`、`announcement`、`role`、`ownerId`、`canSend`、`canDelete`、`canModerate`、`mutedUntil`、`removed`、`unread`、`mentioned`。私聊附带 `peer`，有消息时 `last` 含 `id`、`preview` 和 Unix 秒 `createdAt`。私有对话引用和图片只可在所属对话内使用。事件包含 `{seq,type,payload}`，其中 `message` 携带消息，`delete` 携带 `{id}`。

## 好友、群成员与举报

| 方法 | 路径 | 请求和响应 |
| --- | --- | --- |
| GET | `/social/users` | 用 `q` 按公开昵称搜索，支持 `offset`。返回 `{users,more}`。 |
| GET | `/social/contacts` | 返回 `{contacts,invites}`。好友状态包含 `incoming`、`outgoing`、`accepted`、`removed`、`declined`、`cancelled`。 |
| GET | `/social/contacts/{id}` | 查看当前用户与目标的关系，返回 `{user,state,canReport}`。 |
| PUT | `/social/contacts/{id}` | 传 `{action}`，支持 `request`、`accept`、`decline`、`cancel`、`remove`。 |
| GET | `/social/conversations/{id}/members` | 按昵称 `q` 查询成员，支持 `offset`。群管理者可用 `removed=1` 查已移出成员。 |
| PUT | `/social/conversations/{id}/members/{userId}` | 群管理者传 `{action,seconds?}`，支持 `mute`（1 秒到 30 天）、`unmute`、`remove`。群主还可用 `promote`、`demote`。 |
| POST | `/social/conversations/{id}/invites` | 群管理者传 `{userId}` 邀请好友加入自建群，或邀请已移出的账号重返世界聊天。 |
| PUT | `/social/conversations/{id}/invites` | 受邀用户传 `{status:"accepted"}` 或 `{status:"declined"}` 处理邀请。 |
| POST | `/social/contacts/{id}/report` | 传 `{reason}` 举报现任或曾经的好友，原因最多 500 字。返回 `{id}`。 |
| GET | `/admin/social/reports` | 站点审核人员按 `status`（`pending`、`resolved` 或 `dismissed`）及 `before` 查看举报。返回 `{reports,nextBefore}`。 |
| PUT | `/admin/social/reports/{id}` | 站点审核人员传 `{status:"resolved"}` 或 `{status:"dismissed"}` 处理举报。 |

群权限以当前成员身份为准。群管理员可管理普通成员，群主可管理管理员。用户被移出自建群后，其历史、图片和事件访问立即失效。群级角色独立于站点举报审核权限。

维护者可用 `python3 tools/set-world-chat-roles.py --owner <登录名> --admin <登录名>` 初始化官方群角色。命令使用后端同一数据库配置，要求两个已有启用账号。
