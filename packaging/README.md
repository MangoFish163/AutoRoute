# Windows 发布维护

构建机：Windows x64、Python 3.12 x64、Node.js 24、Rust stable，以及 Rust MSVC 工具链所需的 Visual Studio C++ Build Tools。最终用户无需这些工具。

在项目根目录运行：

```powershell
.\scripts\build-windows.ps1 -Python C:\Path\To\Python312\python.exe
```

脚本创建隔离构建环境，使用锁定依赖，执行后端测试、PyInstaller 冻结、独立后端冒烟检查和 Tauri NSIS 构建。输出为 `release/win-x64-setup.exe` 与 `release/SHA256SUMS.txt`。仅保留轻量联网安装版：WebView2 使用 `downloadBootstrapper`，系统已有运行时则复用，缺少时自动下载并补装；不把完整运行时安装程序打入包中。

在干净的测试机上添加 `-TestInstaller` 可执行真实 NSIS 路径回归和安装/修复/卸载验证；它会临时创建当前用户的卸载登记和开始菜单入口，完成后移除。发现已安装的正式版本时会拒绝运行。CI 默认执行这组检查。测试不会启动桌面实例，后端使用随机空闲端口与隔离数据目录。

`tauri.conf.json` 用于开发，`tauri.release.json` 追加冻结后的后端资源，避免开发运行必须先打包后端。不要直接把默认配置构建出的无后端安装器用于分发。

更新版本时同步修改 `frontend/package.json`、`frontend/src-tauri/Cargo.toml` 和 `frontend/src-tauri/tauri.conf.json`，刷新 npm/Cargo 锁文件。后端版本从前端 package.json 读取，打包时写入私有资源。

发布依赖锁定在 `requirements-windows.lock`，包含全部传递依赖和构建工具。变更依赖后，在 Python 3.12 x64 的干净构建环境解析、更新锁文件并重新验收。不要将开发者机器随意升级后的 freeze 直接用于发布。

NSIS 模板基于 Tauri CLI **2.12.4**：
https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.4/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi

升级 Tauri CLI 时，应比较上游 installer.nsi 的变化，同步安全修复与模板变量，再验证自定义目录、升级和卸载；不能只更新 npm 版本。上游模板采用 Apache-2.0/MIT 双许可证，见目录内的许可证。

卸载数据清理只处理固定的应用自管目录；`LOCAL_MODEL_ROUTER_HOME` 指向的自定义外部目录由用户自行管理。升级不执行数据清理。程序目录依照旧安装器的文件清单删除，未知文件保留。

当前未引入数据库 schema 变更；后续引入变更前，需要补齐 SQLite 在线备份、schema 版本校验与事务迁移。覆盖升级使用旧卸载清单清除程序后安装新版本，不提供程序文件的自动事务回滚；用户数据始终保留，安装失败可重新运行安装器恢复程序。

CI 手动触发或推送与版本匹配的 `v*` 标签都会执行云端构建，并自动公开发布 **未签名 Pre-release**，附安装器与 SHA256 校验值；不标为 Latest 稳定版。手动触发不填 `release_tag` 时自动使用 `v<version>-unsigned.<run_number>`。上传期间短暂创建草稿，全部资产上传成功后才自动公开，最终不需要人工点发布。

手动操作：GitHub 仓库 → Actions → Windows unsigned prerelease → Run workflow。标签操作：更新三处版本号及锁文件后，推送例如 `v0.1.1` 或 `v0.1.1-unsigned.1`。已经公开的版本不会被自动覆盖；重跑前需检查留下的草稿，或者使用新标签。

该工作流显式关闭签名，不读取签名 Secrets，缺少证书不会阻止构建或发布。发布文案说明未签名和 Windows 应用控制可能拦截正常卸载的限制。将来获得 SignPath 后再接入独立的签名与正式发布流程。

公开分发前在隔离的 Windows 10/11 x64 虚拟机中验证：无开发工具、缺少 WebView2 时联网补装、下载失败后的错误提示与重试、中文/空格路径、8787 冲突、进程异常退出、默认卸载、彻底卸载、同版本修复和覆盖升级。构建环境的 PATH 隔离检查不能替代干净系统验证。

### 签名与正常卸载验收

在启用应用控制的 Windows 上，NSIS 复制到 `%TEMP%\~nsu*.tmp\Un.exe` 的卸载程序也需要满足系统的签名策略。仅签名 setup.exe 不足以覆盖这个入口。2026-11-03 的 CodeIntegrity 3077/3033 事件已确认该临时卸载器因签名要求被拦截。

构建现支持从当前用户证书库读取已准备的代码签名证书：

```powershell
$env:LMR_CERTIFICATE_THUMBPRINT = '<代码签名证书指纹>'
$env:LMR_TIMESTAMP_URL = '<证书签发方提供的 RFC3161 时间戳 URL>'
.\scripts\build-windows.ps1 -RequireSigning -TestInstaller
```

先签名后端 EXE；Tauri 使用相同证书签名桌面程序、NSIS 生成的卸载器和最终安装器。脚本检查签名及时间戳；安装测试还会验证安装后的卸载器签名。证书必须具备代码签名用途、可访问的私钥，且被目标系统策略信任；不创建自签名证书或修改信任策略。

本地显式使用 `-RequireSigning` 的构建仍强制要求签名；未签名 Pre-release 工作流不传这个要求，也不消费证书配置。未来的签名发布环境必须事先配置证书及密钥访问方式。私钥和证书密码不进入仓库或聊天。

安装测试必须通过正常 `uninstall.exe /S` 路径启动，等待临时子程序完成并检查卸载登记消失。`_?=` 仍是 NSIS 在升级中使用的内部参数，但不能用于替代用户卸载入口验收。卸载失败时保留测试安装目录和登记用于排查，不再递归删除测试目录掩盖失败。

签名能够提供可验证的发布者身份，不能保证目标机器的所有应用控制策略自动允许该程序；最终仍须在目标策略环境中验证正常卸载。目前没有可用签名证书，尚未生成或验收签名修复包。

应用鉴权、模型凭据与 GitHub 发布令牌的区别见 [AUTHENTICATION.md](AUTHENTICATION.md)。

## 发布附件命名规则

后续发布文件只表达系统、架构和包类型，不包含应用名称和版本号。版本号保留在 GitHub Release 标题、标签及程序元数据中；同名附件在不同 Release 中彼此独立。已公开历史附件不追溯改名。

| 系统与包类型 | 文件名 |
|---|---|
| Windows x64 安装器（当前实现） | `win-x64-setup.exe` |
| Windows ARM64 安装器（未来） | `win-arm64-setup.exe` |
| Linux x64 AppImage（未来） | `linux-x64.AppImage` |
| Linux ARM64 AppImage（未来） | `linux-arm64.AppImage` |
| Debian/Ubuntu x64 包（未来） | `linux-x64.deb` |
| RPM 系列 x64 包（未来） | `linux-x64.rpm` |
| macOS Apple Silicon 磁盘映像（未来） | `macos-arm64.dmg` |
| macOS Intel 磁盘映像（未来） | `macos-x64.dmg` |
| macOS 双架构磁盘映像（未来） | `macos-universal.dmg` |

扩展规则为 `<系统>-<架构>[-<用途>].<格式>`。Windows 安装器保留 `setup`；Linux/macOS 的扩展名本身说明包类型。若未来同时发布 PKG，使用 `macos-arm64.pkg` 等名称。`SHA256SUMS.txt` 保持固定名称，其中引用本次实际发布的短文件名。构建产物上传步骤只包含指定安装器和校验文件，避免混入本地旧名称产物。
