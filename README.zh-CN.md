[English](README.md) | 简体中文

# Token Ledger · 原生桌面用量账本

面向 **macOS 和 Windows 的桌面程序**，使用 Tk 原生窗口、菜单、表格和文件选择器，直接读取本地账本，不需要浏览器，也不启动 Web 服务器。

按 AI harness／客户端分别统计输入、输出、缓存和推理 token，提供日期筛选、每日趋势、模型统计、会话搜索和 CSV 导出。支持 Claude Code、Codex、agy / Antigravity、OpenCode、Gemini CLI、Cline、Roo Code 的本机记录，以及 Cursor 官方用量 CSV 导入。

## 启动桌面程序

### macOS 应用

推荐使用经典拖拽安装包 **`dist/TokenLedger-2.2.0-macos-arm64.dmg`**：

1. 双击 DMG，打开安装窗口。
2. 把左侧 **Token Ledger** 拖到右侧 **Applications（应用程序）** 文件夹。
3. 推出安装磁盘，再从“应用程序”打开 Token Ledger。

更新前请先退出旧版。账本保存在用户数据目录中，替换应用不会清除账本。

双击实际构建产物：

```text
dist/macos-arm64/Token Ledger.app
```

这是包含 Python、Tcl/Tk 和时区数据的原生 arm64 应用，仅面向 Apple Silicon Mac；运行打包应用无需另外安装 Python。可以将 `.app` 复制到自己选择的位置。构建方式和签名状态见 [桌面打包说明](packaging/README.md)。

### Windows

使用 **`dist/TokenLedger-2.2.0-windows-x64-setup.exe`**（Windows 10 / 11，64 位）：

1. 双击安装包，进入英文安装向导，点击「Next」。
2. 选择安装位置，默认安装到当前用户的程序目录，不需要管理员权限。
3. 选择是否创建桌面快捷方式，点击「安装」。
4. 安装完成后可勾选「Launch Token Ledger」，点击「Finish」。以后也可从开始菜单打开。

安装包自带 Python、Tcl/Tk 和时区数据，安装及运行都无需另外下载这些组件。可从 Windows「设置 → 应用」或开始菜单卸载；**卸载保留用量账本和设置**。升级前请先退出程序。

当前 `.exe` 已在 macOS 上通过 NSIS 交叉构建和文件核对，**尚未在真实 Windows 系统完成安装／运行验证**。Windows CI 已加入安装、原生界面自检、卸载和数据保留测试。构建记录见 `dist/windows-installer-info.json`。

源码版也可双击 `启动用量统计.bat`，或在项目目录运行：

```powershell
py -3 -m pip install .
py -3 -m tokenlog
```

源码版需要 Python 3.10 或更高版本及 Tcl/Tk。建议使用 python.org 安装器，并保留 Tcl/Tk 组件；项目依赖中的 `tzdata` 用于 Windows 上的 IANA 时区。可先用 `py -3 -c "import tkinter"` 检查 Tk。

### 从源码运行

macOS 源码版可双击 `启动用量统计.command`，或使用带 Tk 的 Python：

```sh
python3 -c "import tkinter"
python3 -m pip install .
python3 -m tokenlog
```

如果默认 Python 缺少 `_tkinter`，改用 python.org 提供的带 Tk 解释器。也可在虚拟环境安装后，使用该环境的 Python 运行。关闭桌面窗口即停止本程序的扫描与刷新。

## 界面语言

界面默认使用 **English**。打开 **Settings → Display & refresh → Language / 语言**，选择 **简体中文**，点击 **Save**，界面立即切换，下次启动保留选择。也可用相同设置切回 English。

切换语言会保留日期、来源、搜索条件、账本和自定义 API 单价；项目名、路径与会话 ID 保留原文。

## 界面操作

1. 首次启动等待扫描完成。默认显示全部历史；可切换「今日 / 近 7 天 / 本月 / 自定义」以及具体来源。
2. 在「各工具独立统计」查看每个 harness 的精确计数；双击来源可筛选。模型名称另作维度，例如 OpenCode 中调用 Claude 仍归入 OpenCode 来源。
3. 使用「会话明细」「模型用量」标签查看详情，搜索项目或会话，双击会话打开明细。
4. 在「数据覆盖与口径」查看缺少 usage、无法确认缓存含义、未知模型或不受支持格式等提示。缺少记录不等于没有使用。
5. 点击「导出 CSV」保存当前日期和来源范围的用量记录。默认每 15 秒刷新，可在「数据源与设置」修改。

输入包含缓存读取和写入，输出包含推理；这些子计数不能再次加到合计。「记录条数」是去重后的用量记录数，不保证等于真实 API 请求次数。

## 接入更多 harness

「数据源与设置」可修改内置工具路径、启用／停用采集器，或添加自定义来源。七种本机采集器自动扫描已知位置；**Cursor 默认停用，需从官方 Usage 页面导出 CSV，选择文件并启用。**

其他工具可以导入明确格式的 **OpenAI / 兼容 API usage、Anthropic usage、Gemini GenerateContent usageMetadata 或 Token Ledger 标准记录 v1** 的 JSONL / JSON。选择具体文件或目录、独立来源 ID 和名称，再选择对应格式。程序需要原始用量字段，无法只根据聊天文本还原真实 token、缓存命中或隐藏推理。

可用 [合成示例](examples/tokenlog-usage.sample.jsonl) 验证导入：新增 ID 为 `sample-harness` 的来源，选择「Token Ledger 标准记录 v1」和该文件，保存后查看「全部历史」。预期为 **2 条记录、1 个会话、1,700 token**；示例 ID 均以 `sample` 开头，与真实日志无关。

具体路径和覆盖限制见 [支持的 harness](docs/SUPPORTED_HARNESSES.md)。适配器可扩展；这不表示任意工具的任意版本都能自动取得完整历史。

## 本机数据与时区

账本与设置保存在当前用户的标准应用数据目录，独立于安装目录：

| 系统 | 默认数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/TokenLedger/` |
| Windows | `%LOCALAPPDATA%\TokenLedger\` |

`token-usage.sqlite3` 保存账本，`settings.json` 保存数据源和偏好。菜单「文件 → 打开数据文件夹」可查看。默认使用本机时区 `local` 并处理历史夏令时，也可指定 IANA 时区名称。

原日志只读，账本只保存计数、时间、模型、会话 ID、项目和来源路径等元数据，不保存对话正文或认证凭据。原日志移走、删除或截断后，已采集历史仍保留；停用或移除数据源也保留历史。程序不调用模型或向外部服务上传用量；发布源码包不包含私人日志、账本或本机配置。

这是**本机已保存记录的用量**，不是账号完整用量、订阅剩余额度或费用账单。其他设备、普通网页聊天、没有保存 usage 的调用不在完整覆盖范围内。

## 命令行与构建

不打开界面，扫描一次并输出 JSON：

```sh
python3 -m tokenlog --report --provider codex --start 2026-11-01 --end 2026-11-01
```

Windows 使用 `py -3 -m tokenlog ...`。可用 `--data-dir`、`--db` 指定数据位置，用 `--timezone local` 或 IANA 名称指定时区。迁移早期项目账本可传 `--migrate-from <原账本路径>`，原账本保留，已有目标账本不会被覆盖。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_desktop.py --source-only
```

macOS `.app` 在 macOS 构建；Windows 安装向导可使用 NSIS 跨平台构建，安装与运行验收须在 Windows 执行。完整命令、干净源码 ZIP、产物审计和 CI 说明见 [packaging/README.md](packaging/README.md)。

## v2.2 英文默认与中文切换

- macOS 与 Windows 程序默认英文，可在设置中切换简体中文，立即生效并保存。
- 主界面、图表、单次调用与价格明细、数据源设置、系统提示均随语言切换。
- Windows 使用英文安装向导；macOS 继续使用拖入 Applications 的 DMG。

## 图表与 API 等价值

- 图表为每个 Harness 显示固定颜色图例；鼠标移到柱上显示该日期或合并日期段的来源分项。
- 顶部显示所选时期的预计 API 等价值（USD），会话和模型表也有对应金额。
- “逐次调用与估值”支持搜索、翻页和双击明细，列出普通输入、缓存读取、缓存写入、输出的 token、单价及分项金额。推理已包含在输出中。
- 缓存写入 TTL 有原始记录时按对应价格计算；时长缺失时显示短时到长时的范围。未知模型标为未定价，不会静默套用相似模型。
- “API 单价”展示官方来源与核验日期，也允许为任意模型填写自定义价格。
- 估值按 2026-11-01 价目快照统一计算历史用量，不代表历史实付或订阅账单，不含工具、税、服务等级、地区及显式缓存存储时长等费用。

官方依据和公式见 [API 估值说明](docs/API_PRICING.md)。

## 许可证

本项目源码采用 [MIT License](LICENSE)。随安装包分发的第三方运行环境保留各自许可证。
