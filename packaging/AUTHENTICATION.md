# 本地鉴权与凭据流转

当前应用是可信本机上的个人模型网关，没有用户注册、登录会话、OAuth 或多用户权限体系。

## 组件和请求流程

1. `backend/app.py` 的 `lifespan` 初始化 SQLite；首次启动生成 `sk-local-` 加随机字符串的本地客户端 Key，写入数据目录的 `client.key`，已有 Key 不覆盖。
2. 下游客户端调用 `/v1/models`、`/v1/chat/completions` 或 `/v1/messages`。`require_client_key` 使用 `secrets.compare_digest` 比较传入值与本地 Key，失败返回 HTTP 401。
3. OpenAI 路径读取 Authorization；Anthropic 路径优先读取 `x-api-key`，没有时回退 Authorization。当前校验实现去掉可选的 `Bearer ` 前缀，因此也接受 Authorization 中直接传入 Key；它不是严格的 Bearer 格式解析器。
4. 校验通过后，路由层按逻辑模型选择部署，`choose_credential` 从可用上游凭据中轮转选择。上游凭据与本地客户端 Key 相互独立。
5. `forward` / `forward_stream` 向 Anthropic 写入 `x-api-key`，向 OpenAI / Responses 写入 `Authorization: Bearer …`。下游传入的本地 Key 不作为上游密钥透传。

## 凭据存储与界面

- 上游 Token 保存到 SQLite 前调用 `protect_secret`：Windows 用当前用户的 DPAPI 加密，并加 `dpapi:` 标记；发送请求前用 `reveal_secret` 在内存中解密。跨 Windows 用户迁移数据库不能保证解密。
- 非 Windows 开发环境以 `plain:` 明文保存；早期无标记的记录也按明文兼容读取。不得把该开发回退理解为加密。
- AI 调优模型的 Key 使用同一套保护函数；设置返回 `key_saved` 而不是原密钥。
- `safe_deployment` 返回密钥数量及末四位掩码，`redact` 对已知密钥进行错误信息脱敏；脱敏不等同于对所有未知敏感内容的全面保证。
- 本地 `client.key` 本身是明文文件，并通过 `/api/bootstrap` 返回给管理界面，以供用户查看、复制。它不采用 DPAPI，也没有自动过期、刷新令牌或独立撤销列表。

## 当前信任边界

`/api/*` 管理接口（含读取本地 Key、修改配置和测试供应商连接）没有独立管理鉴权；`/health` 也不要求 Key。前端管理请求不会携带登录会话。Tauri 来源 CORS 白名单不构成服务端鉴权。

桌面入口只绑定 `127.0.0.1`，适合可信本机使用；不要转发或公开该端口。能以同一 Windows 用户读取文件、访问本机管理接口的程序处于当前信任边界内。本地 API Key 无法单独保护管理接口。

桌面健康检查的实例标识用于识别本次启动的后端，不是登录凭据；后台退出使用继承的私有 stdin 管道。代码签名解决发布者和二进制完整性，与 API 鉴权是两套独立机制。

## GitHub 发布凭据

未签名预发布工作流使用 GitHub 自动生成的 `GITHUB_TOKEN`（作为 `GH_TOKEN` 注入）上传资产和发布 Pre-release；不使用模型密钥或个人访问令牌，也不依赖 SignPath。工作流不触发 PR 代码的发布步骤，不读取代码签名证书配置，测试使用一次性数据和随机端口。
