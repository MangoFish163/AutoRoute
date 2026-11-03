# 本地模型路由器

普通 Windows 用户请前往 [Windows 安装版发布页](https://github.com/MangoFish163/AutoRoute/releases) 下载 `win-x64-setup.exe`（自动发布的未签名测试版位于 Pre-release）。双击安装，可选择应用位置；无需 Python、Node.js 或开发环境。首期目标平台为 Windows 10/11 x64。

当前本地产物仍是未签名测试包：已确认它的临时卸载程序在本机应用控制策略下被拦截，正常卸载尚未验收通过，不应作为正式发布。签名构建和正常卸载测试已补齐，仍需配置符合目标系统策略的代码签名证书。详情见 [验收记录](packaging/验收记录.md)。

安装后从桌面或开始菜单打开；关闭窗口会隐藏到托盘，右键托盘选择“退出应用”可停止网关。卸载入口位于 Windows“设置 → 应用”，默认保留配置，可勾选删除模型配置、密钥和历史记录。自 0.1.1 起，桌面端启动时自动检查测试版更新，也可在“系统设置 → 应用更新”中查看说明、下载并确认重启安装，沿用原目录并保留数据。旧版需要先手动安装一次支持更新的新版。稳定版通道预留给后续正式发布，目前未启用。

仅提供轻量安装版，内置后端运行时和依赖；检测到系统缺少 WebView2 时，安装器自动联网下载并补装，无需用户手动配置环境。安装和访问模型供应商需要网络连接，使用模型还需有效上游密钥。

构建与发布维护见 [Windows 发布说明](packaging/README.md)，完整产品方案见 [Windows 安装与分发设计](Windows安装与分发设计.md)。

Windows 单机个人使用的本地模型网关。支持多个 OpenAI/Anthropic 协议部署、多个 Token 轮换、代理配置、基础路由权重、失败冷却、SQLite 请求日志和 AI 调优审计。

## 当前实现范围

- 入站：OpenAI `POST /v1/chat/completions`、`GET /v1/models`；Anthropic `POST /v1/messages`。
- 支持同协议上游的 SSE 流式透传；跨协议非流式消息转换；OpenAI Chat Completions 转接上游 Responses 的普通与流式请求。
- 工具调用在非流式常见结构中做基础映射；多模态、复杂工具事件和供应商扩展尚未全面兼容。
- 多 Token 按轮转选择；失败先降低自动权重，至少连续 3 次请求失败且失败前的生效权重低于 50 才允许冷却，401/403/429 的密钥冷却也遵守该限制。生效权重 ≥50 时保持可重试，即使本次降到 50 以下，也从下一次失败开始判断冷却；手动锁定的权重不被自动修改。成功请求按每 20 个请求渐进上调自动权重。
- 每次点击保存部署，即使配置没有变化，也会解除该部署及所有密钥的冷却、清零连续失败次数、清除密钥轮转位置，并将自动权重因子重置为 1；基础权重、历史统计和日志保留。保存前发出的请求返回结果后不再影响新配置的运行状态。流式请求在结束时只计一次成功或失败。
- AI 分析手动触发并发送脱敏的配置/统计摘要；“AI 建议调权”仅能更新自动权重，并校验部署、锁定状态、范围、重复部署和单次变化上限。
- Windows 上游 Token 使用 DPAPI 加密后存入 SQLite；非 Windows 开发环境仅用于本地调试，使用 `plain:` 标记。
- “启用环境代理”为模型的代理总开关：未勾选时始终直连，忽略自定义 URL、系统代理和环境代理变量；勾选且填写自定义代理 URL 时使用该地址，留空时读取 Windows 当前用户 Internet Settings 中启用的静态 HTTP/HTTPS 代理（未配置系统代理时使用 HTTP 客户端环境代理变量）。普通请求、流式请求、连接测试和模型列表获取使用同一规则。PAC/WPAD 自动代理脚本暂不支持。
- 规则依据真实请求被动调整；当前尚无后台定时探测任务或 Redis 缓存。
- Tauri 原生桌面窗口、独立后端运行包、可选择目录的中文 Windows 安装器，以及默认保留配置的卸载流程。

## 开发者启动方式

需要 Python 3.11+、Node.js 20+ 和 npm。可以运行根目录的 `start.ps1` 自动创建虚拟环境、安装依赖并启动本地服务；运行 `stop.ps1` 停止服务。也可以手动启动：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# 仅用于手动浏览器开发；桌面版和 start.ps1 自动维护管理令牌。
$env:LOCAL_MODEL_ROUTER_MANAGEMENT_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
python desktop_entry.py --port 8787
```

另开 PowerShell 窗口运行前端：

```powershell
cd frontend
npm install
npm run dev
```

`start.ps1` 会打印并打开实际 UI 地址，并自动配置本次开发会话的管理认证；后端优先使用已保存端口，冲突时自动选择空闲端口并保存；重复运行开发脚本会提示复用现有会话。前端默认端口从 `5173` 开始，如果端口已被占用会自动选择下一个空闲端口。Vite 会将 `/api`、`/v1` 和 `/health` 请求代理到本地后端。手动启动时，通过 `http://127.0.0.1:5173/#management_token=<上述环境变量的值>` 打开页面；页面立即移除 fragment，并在当前标签页保留认证。也可以先运行 `npm run build`，再启动后端，通过 `http://127.0.0.1:8787/#management_token=<上述环境变量的值>` 打开管理 UI。不要分享含令牌的 URL。

### 原生桌面窗口（开发版）

需要 Rust/Cargo、Python 3.11+、Node.js 20+。首次可运行 `start.ps1` 准备 Python 与前端依赖，随后运行 `stop.ps1` 停止浏览器版服务，再运行根目录的 `start-desktop.ps1`。Tauri 会从 `5175` 至 `5200` 自动选择空闲端口。点击窗口关闭按钮会隐藏到 Windows 系统托盘，托盘图标左键可重新打开窗口，右键菜单可退出应用；退出时会停止桌面端自己启动的后端。后端默认使用 8787，之后优先使用已保存端口；端口被占用或被系统禁止绑定时自动选择空闲端口并保存，界面提示更新客户端地址。不会复用或停止占用端口的其他服务。桌面端启动的后端日志写入 `%LOCALAPPDATA%\LocalModelRouter\desktop-backend.log`。

该入口用于本地开发；独立安装版请使用根目录 `scripts/build-windows.ps1` 构建。发布构建通过 PyInstaller 封装后端并使用 `tauri.release.json` 打包，不依赖用户的系统 Python。

服务第一次启动后会生成本地客户端 API Key。管理页面“概览”中可查看 Base URL 和 Key。默认上游数据目录为 `%USERPROFILE%\.local-model-router`，包括 SQLite 数据库和本地客户端 Key。

启动时会自动创建数据库和数据表，并在缺少厂商预设记录时写入 7 个默认厂商；旧版本未初始化预设的数据库也会自动补齐。已有预设以数据库为准，重启不会覆盖修改或恢复已删除的厂商。厂商预设只提供添加模型的默认参数，实际模型部署仍需配置上游地址、模型和 API Key。

## 客户端配置

将客户端的 OpenAI Base URL 设置为管理页面复制的实际 API 地址（初始默认 `http://127.0.0.1:8787/v1`），API Key 使用页面显示的本地 Key。Anthropic 客户端使用同一地址去掉末尾 `/v1`，并使用同一个本地 Key。

在“系统设置 → 本地网关 → 下次启动端口”中可设置 1024～65535 的端口，保存后下次启动生效；当前请求不受影响。请从托盘退出应用再打开，关闭窗口只会隐藏到托盘。配置保存在数据目录的 `network.json`，升级后保留。遇到冲突会先尝试后续 100 个端口，再由系统分配空闲端口，并保存最终选择；后续启动复用该端口。端口切换后必须更新外部客户端的 Base URL，页面接入示例与复制地址会同步更新。

所有正式入口和 `start.ps1` 均使用实际绑定的端口。手动运行 `desktop_entry.py --port <端口>` 可固定本次端口（冲突时报错，不修改保存配置）；手动运行 Vite 时通过环境变量 `LOCAL_MODEL_ROUTER_BACKEND_URL` 指定后端地址。

请求的 `model` 字段使用面板中的“逻辑模型别名”。多个部署可以配置相同别名，网关按可用状态和权重挑选上游。

### 豆包 / 火山方舟 Responses 模型

在“添加模型 → 自定义模型”中选择 API 格式 **Responses（豆包 / OpenAI）**，Base URL 填 `https://ark.cn-beijing.volces.com/api/v3`，模型 ID 填实际开通的模型（例如 `doubao-seed-evolving`），密钥在界面中输入。也可填写完整地址 `https://ark.cn-beijing.volces.com/api/v3/responses`；已有 OpenAI 格式部署的地址以 `/responses` 结尾时会自动识别该协议。厂商预设也可选择 Responses 格式。

客户端继续使用本地 `/v1/chat/completions` 和逻辑模型别名。网关将 `messages`、图片、函数工具及结果、输出 Token 上限转换为 Responses 参数，并将输出文本、推理摘要、工具调用及流式 SSE 转回聊天格式。原始 usage（含缓存和推理 Token 明细）保存在请求日志中。Anthropic 入站支持非流式转接；跨协议流式暂不支持。当前适配文本、图片和函数工具，视频、音频等内容类型暂不支持。连接测试使用同一 API 前缀下的 `/models`，不会把 `/models` 拼在 `/responses` 后面。

参数与事件格式参照[火山方舟 Responses API 文档](https://console.volcengine.com/ark/region%3Acn-beijing/docs/create-model-responses-api)。

## 重要说明

- 仅面向可信的本机使用，服务默认监听 `127.0.0.1`。
- 管理 API 使用应用自动维护的独立管理令牌，无需用户登录或填写；模型客户端继续使用本地客户端 Key。虽然服务仅绑定回环地址，仍请勿将端口转发或暴露到局域网/公网。
- OpenAI/Anthropic 协议存在较多细节差异。当前支持 Chat Completions 到上游 Responses 的流式转换，但不提供入站 `/v1/responses`，也不支持 Anthropic 跨协议流式、完整多模态转换和全部供应商特性。
- 429 的 `Retry-After` 数字秒格式会被读取；HTTP 日期格式暂使用默认冷却。
- 模型 RPM/Token 上限是人工维护的元数据；不同 Token 可能共享供应商账号额度，轮换不保证提高实际限额。
- AI 调优只根据脱敏的近期统计和配置摘要运行。请确认已获准向所配置的分析模型服务发送这些摘要。

## 技术结构

- `backend/app.py`：FastAPI、本地路由、供应商协议适配、SQLite、规则与 AI 审计。
- `frontend/src/main.jsx`：React 管理面板与配置交互。
- `frontend/src/components/ui`：本地可编辑的 shadcn/ui 风格 Button/Badge 基础组件。
- `frontend/src/style.css`：本地仪表盘样式。
- `frontend/src-tauri`：Tauri Windows 桌面窗口外壳。
- `start-desktop.ps1`：启动原生桌面开发窗口。
- `项目设计方案.md`：完整设计、范围、数据模型、阶段与验收标准。
