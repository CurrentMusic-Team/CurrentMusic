# 服务状态邮件退订

状态通知邮件内的「退订服务状态邮件」按钮会打开确认页。确认后，同一邮箱停止接收后续服务状态通知，包括仍在等待投递的通知。验证码邮件继续按账号操作发送。

| 方法与路径 | 参数 | 响应 |
| --- | --- | --- |
| `GET /cm/status/email/unsubscribe` | URL 查询参数 `token` | 200 HTML 确认页或已退订提示。打开链接不会改动订阅状态 |
| `POST /cm/status/email/unsubscribe` | `application/x-www-form-urlencoded` 表单字段 `token` | 200 HTML 已退订提示。重复提交会得到相同结果 |

无效链接返回 404。令牌对应收到通知的邮箱，不要求登录。反向代理应关闭该路径的请求日志和缓存。部署到其他域名时可用 `CM_STATUS_MAIL_ORIGIN` 指定退订页的 HTTPS origin。保持状态邮件投递进程与后端连接同一数据库。
