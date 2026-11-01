# 本地模型路由器（初版）

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
- 新增 Tauri 原生桌面窗口开发入口；Windows 安装包目前仍是后续工作，需将 Python 后端及其运行时一起打包。

## 启动方式

需要 Python 3.11+、Node.js 20+ 和 npm。可以运行根目录的 `start.ps1` 自动创建虚拟环境、安装依赖并启动本地服务；运行 `stop.ps1` 停止服务。也可以手动启动：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8787
```

另开 PowerShell 窗口运行前端：

```powershell
cd frontend
npm install
npm run dev
```

`start.ps1` 会打印并打开实际 UI 地址；默认端口从 `5173` 开始，如果端口已被占用会自动选择下一个空闲端口。Vite 会将 `/api`、`/v1` 和 `/health` 请求代理到本地后端。也可以先运行 `npm run build`，再启动后端；后端检测到 `frontend/dist` 后会直接提供管理 UI（`http://127.0.0.1:8787`）。

### 原生桌面窗口（开发版）

需要 Rust/Cargo、Python 3.11+、Node.js 20+。首次可运行 `start.ps1` 准备 Python 与前端依赖，随后运行 `stop.ps1` 停止浏览器版服务，再运行根目录的 `start-desktop.ps1`。Tauri 会从 `5175` 至 `5200` 自动选择空闲端口。点击窗口关闭按钮会隐藏到 Windows 系统托盘，托盘图标左键可重新打开窗口，右键菜单可退出应用；退出时会停止桌面端自己启动的后端。若 8787 端口已有服务，桌面窗口会复用它，不会擅自终止该外部进程。桌面端启动的后端日志写入 `%LOCALAPPDATA%\LocalModelRouter\desktop-backend.log`。

该入口目前用于本地开发，不代表已完成可独立分发的 Windows 安装版。安装包需要进一步集成独立 Python 运行时和冻结后的后端依赖；现在打包后直接运行仍可能依赖系统 Python 与已安装依赖。

服务第一次启动后会生成本地客户端 API Key。管理页面“概览”中可查看 Base URL 和 Key。默认上游数据目录为 `%USERPROFILE%\.local-model-router`，包括 SQLite 数据库和本地客户端 Key。

启动时会自动创建数据库和数据表，并在缺少厂商预设记录时写入 7 个默认厂商；旧版本未初始化预设的数据库也会自动补齐。已有预设以数据库为准，重启不会覆盖修改或恢复已删除的厂商。厂商预设只提供添加模型的默认参数，实际模型部署仍需配置上游地址、模型和 API Key。

## 客户端配置

将客户端的 OpenAI Base URL 设置为 `http://127.0.0.1:8787/v1`，API Key 使用管理页面显示的本地 Key。Anthropic 客户端设置 Base URL 为 `http://127.0.0.1:8787`，并使用同一个本地 Key。

请求的 `model` 字段使用面板中的“逻辑模型别名”。多个部署可以配置相同别名，网关按可用状态和权重挑选上游。

### 豆包 / 火山方舟 Responses 模型

在“添加模型 → 自定义模型”中选择 API 格式 **Responses（豆包 / OpenAI）**，Base URL 填 `https://ark.cn-beijing.volces.com/api/v3`，模型 ID 填实际开通的模型（例如 `doubao-seed-evolving`），密钥在界面中输入。也可填写完整地址 `https://ark.cn-beijing.volces.com/api/v3/responses`；已有 OpenAI 格式部署的地址以 `/responses` 结尾时会自动识别该协议。厂商预设也可选择 Responses 格式。

客户端继续使用本地 `/v1/chat/completions` 和逻辑模型别名。网关将 `messages`、图片、函数工具及结果、输出 Token 上限转换为 Responses 参数，并将输出文本、推理摘要、工具调用及流式 SSE 转回聊天格式。原始 usage（含缓存和推理 Token 明细）保存在请求日志中。Anthropic 入站支持非流式转接；跨协议流式暂不支持。当前适配文本、图片和函数工具，视频、音频等内容类型暂不支持。连接测试使用同一 API 前缀下的 `/models`，不会把 `/models` 拼在 `/responses` 后面。

参数与事件格式参照[火山方舟 Responses API 文档](https://console.volcengine.com/ark/region%3Acn-beijing/docs/create-model-responses-api)。

## 重要说明

- 仅面向可信的本机使用，服务默认监听 `127.0.0.1`。
- 本地初版的管理 API 暂未单独设置管理账户；虽然服务仅绑定回环地址，仍请勿将端口转发或暴露到局域网/公网。
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
