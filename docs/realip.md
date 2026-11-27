# RealIP 透传策略

CurrentMusic 代理网易云接口时，默认以服务器 IP 请求上游。某些场景下需要把**可信的客户端
真实 IP** 作为 `realIP` 传给上游，让网易云按用户网络位置选择 CDN 边缘或执行风控。

## 可信取值链

只使用服务端解析出的 IP，**绝不接受调用方参数里的 `realIP`**：

后端仅对回环代理连接读取 `X-Real-IP`，并兼容旧配置提供的 `X-Forwarded-For` 末跳。
头部值必须是有效的单个 IPv4 或 IPv6 地址。常规直连使用 TCP 对端地址。

Cloudflare 位于 nginx 前时，在站点的 `server` 块启用 `deploy/nginx-cloudflare-realip.conf.example` 配置模板。
nginx 只对 Cloudflare 公布的 IPv4 和 IPv6 网段采信 `CF-Connecting-IP`。
每个转发至 3010 的 `location` 都用 `$remote_addr` 覆盖 `X-Real-IP` 和 `X-Forwarded-For`。
后端 3010 端口仅监听回环地址。更新 [Cloudflare 官方网段](https://www.cloudflare.com/ips/) 时，同步更新
nginx 模板与 `backend/cm_client_ip.py`，运行 `nginx -t` 后重载。Cloudflare 的 Pseudo IPv4 使用关闭或
添加头部模式，以保留 `CF-Connecting-IP` 中原生的 IPv6 地址。

生产排查可比较 nginx 的 `$realip_remote_addr`（TCP 对端）和 `$remote_addr`（还原后的出口地址）。
核验公网探针确实到达源站后，再用已知出口地址对照；CDN 返回 403 时该线路仍待验证。

实现见 `backend/cm_ncm_gateway.py::client_ip()`。网关的
`normalize_params()` 会把客户端提交的 `realIP` 当作保留参数剥离，之后再由服务端注入。

## 用户 IP 属地

同一取值链用于登录、鉴权和属地刷新。属地查询跳过 Cloudflare 边缘和非公网地址，并在无法确认时保留已有属地。
成功结果按 IPv4 `/24` 或 IPv6 `/48` 网段缓存，切换可用网段后立即触发刷新。用户表与公开资料只保存和显示省份或国家。
历史日志中的边缘 IP 无法还原到用户出口。`tools/backfill-region.py --dry` 可预演对空属地的回填，只读取数据并输出汇总。

## 当前会透传的范围

| 范围 | 方法 | 说明 |
|---|---|---|
| 媒体地址族 | GET/POST | `/song/url`、`/song/download/url`、`/mv/url`、`/video/url`、`/song/cloud/download`、`/voice/detail`；上游按 realIP 选就近 CDN 边缘 |
| 搜索 | GET | `/ncm/search`、`/cloudsearch`、`/search` 及其建议/热搜子接口；透传 NGINX 提供的用户 IP，搜索缓存按用户 IP 隔离 |
| 账号写操作（通用转发） | 非 GET | T2/T3 写接口；例如歌单创建/修改/增删曲目/排序、收藏、云盘上传/删除 |
| 网易云红心 | POST | `POST /ncmbind/like/{ncmId}` |
| 关注/取关歌手 | POST | `POST /artists/{id}/follow` |
| 评论、回复、删评 | POST/DELETE | `POST|DELETE /ncmbind/comment/{ncmId}` |
| 评论点赞 | POST | `POST /ncmbind/comment-like/{ncmId}` |

判定函数：`cm_ncm_gateway.should_forward_real_ip(path, method, tier)`。

## 不会透传的范围

- 其余公共缓存只读接口（T0/T1 GET）：歌词、歌曲详情、歌单详情、图片等；
- 按账号 cookie 决定的推荐流、私人歌单、喜欢列表；
- 自建数据库中的属地和其他本站业务字段；
- AI/DeepSeek 请求；
- 图片、封面等静态资源。

搜索沿用音源取 IP 的同一条可信链：传的是用户出口 IP，不能用服务器地址代替。
直连服务器执行维护任务时，请求方才是服务器本身。查询参数 `realIP` 不得覆盖可信请求头。

## 缓存与日志

- 搜索与媒体的 RealIP 在缓存键计算**之前**注入；通用音源缓存不会跨用户 IP 复用。
- 专用搜索缓存、音源地址缓存和最佳音质档位缓存都按用户 IP 隔离。
- 灰色曲目匹配只缓存已验证的成功结果 120 秒，同样按用户 IP 隔离；失败不缓存。
- 写操作不缓存。
- 审计日志只记录接口、用户和参数键，不记录 Cookie/RealIP 值。

## 隐私

透传 RealIP 意味着网易云会看到用户出口 IP，用于搜索地区判断、媒体分发和账号操作风控。
隐私说明与实现保持一致，不向无关第三方转发。RealIP 只影响上游的请求处理，不代表获得音乐版权或播放权限。

`/song/url/match` 的当前上游实现只把歌曲 ID 与可选音源名传给匹配库，不使用 `realIP`。
本站调用它时保留同一用户 IP 参数，但不能据此声称第三方匹配服务会使用该 IP；
也不会改成伪造的国内 IP。匹配路径与校验规则见 [播放音源说明](playback-sources.md)。
