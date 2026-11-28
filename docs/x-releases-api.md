# CurrentMusicX CI 更新元数据

`GET /cm/x/releases?channel=ci` 是公开接口，无需登录。成功返回 HTTP `200` 和 GitHub Releases 格式的 JSON 数组，仅包含 `bileizhen/CurrentMusicX` 已公开的 `vX.Y.Z-ci.N` 预发布。发布说明、APK 下载地址、大小和摘要沿用 GitHub 字段。空数组表示当前没有公开的 CI 发布。

后端将结果缓存 60 秒，同一服务内的并发请求共享一次 GitHub 刷新。GitHub 通过 `ETag` 返回 `304` 时，后端将缓存续期 60 秒。GitHub 限流或刷新失败时，后端继续返回缓存，直到距上次成功验证满一小时。缓存过期且刷新失败，或首次拉取失败时，返回 `503`。客户端可将此接口作为 CI 元数据首选，遇到网络错误、无效响应或 HTTP `503` 时回退到 GitHub Releases API。缺少 `channel` 或值不是 `ci` 时返回 `400`。APK 仍通过客户端选择的下载源获取。

自托管时可在受保护的后端环境文件中设置 `CM_X_RELEASES_GITHUB_TOKEN`，使用对该上游仓库具有只读 Contents 权限的令牌。未配置时使用 GitHub 的匿名配额。GitHub 限流时后端会遵守上游给出的重试时间。
