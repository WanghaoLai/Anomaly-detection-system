# 认证安全与部署说明

## 当前方案

- Access Token：15 分钟，保存在 `HttpOnly` Cookie。
- Refresh Token：7 天，保存在 `HttpOnly` Cookie，每次刷新都会轮换。
- CSRF：`SameSite` Cookie + 双提交 `X-CSRF-Token`。
- 服务端会话：每次登录创建独立会话，注销立即撤销当前会话。
- 全局版本：修改密码或管理员重置密码会递增账号 `token_version`，
  使该账号的全部 Access/Refresh Token 立即失效。
- 每次认证都会查询账号和会话，确认账号存在、角色未变化、版本有效、
  会话未撤销。
- Bearer Access Token 仍可用于非浏览器 API 调用，但网页端不再读取或
  保存 Token。

## 相比 localStorage

`HttpOnly` Cookie 阻止页面 JavaScript 直接读取 Access/Refresh Token，
降低 XSS 导致令牌被直接窃取的风险。Cookie 会自动随请求发送，因此必须
同时使用 `SameSite`、精确 CORS 和 CSRF 校验；本项目已实现这些控制。

XSS 仍可能代表用户发起操作，因此仍需保持输出转义、依赖更新和内容安全
策略。`HttpOnly` 不是 XSS 的替代方案。

## 生产部署要求

1. 全站使用 HTTPS。
2. 设置 `JWT_COOKIE_SECURE=true`。
3. `CORS_ALLOWED_ORIGINS` 只填写真实前端 HTTPS 域名，不能使用 `*`。
4. 保持 `JWT_COOKIE_SAMESITE=lax` 或按同站部署情况使用 `strict`。
   如果必须使用 `none`，系统会强制要求 `Secure=true`。
5. 使用密码学安全随机 JWT 密钥，并通过秘密管理系统注入。
6. 多实例部署共享同一个 MySQL 会话和限流数据库。
7. 定期清理已过期或已撤销的 `auth_session` 以及过期的
   `login_throttle` 记录。

本地开发使用 HTTP 时可设置 `JWT_COOKIE_SECURE=false`；该值不适用于生产。
