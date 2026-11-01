# 支持的 Harness 与覆盖范围

Token Ledger 按运行工具分别统计，模型名称另作维度。例如 OpenCode 中调用 Claude，归入 OpenCode 来源及相应 Claude 模型。macOS 与 Windows 使用同一统计核心和原生桌面界面。

“支持”表示已实现下列记录格式的采集器，不表示所有历史、所有插件版本或所有客户端都有完整 usage。没有原始用量时会提示缺口，不从聊天文本估算并称为精确值。

## 八种内置来源

| 来源 | 接入方式与本机位置 | 覆盖与限制 |
| --- | --- | --- |
| Claude Code | 自动：`~/.claude/projects/**/*.jsonl` | 读取助手消息 usage，按消息身份合并流式记录；未保存调用无法补算 |
| Codex | 自动：`~/.codex/sessions`、`archived_sessions` | 响应记录优先，旧日志使用累计计数增量；处理子 Agent 和分叉历史 |
| agy / Antigravity | 自动：`~/.gemini/antigravity-cli`、`antigravity` 下的 `conversations/*.db` | 读取结构化用量和活动 WAL；根会话 ID 归并子代理记录，未来 protobuf 变化需更新适配器 |
| OpenCode | 自动：`$XDG_DATA_HOME/opencode` 或 `~/.local/share/opencode`；Windows 另查 `%LOCALAPPDATA%\opencode` | 支持新 SQLite `session_message/session_v2`、旧 `message/session`，及旧 `storage/message` JSON |
| Gemini CLI | 自动：`~/.gemini/tmp/**/chats/session*.json`、`.jsonl` | 支持旧 JSON、新 JSONL、`GEMINI_CLI_HOME` 和沙盒缓存位置；有文件不一定有 usage |
| Cline | 自动：编辑器 `User/globalStorage/saoudrizwan.claude-dev/**/ui_messages.json`，另查 `~/.cline/data` | 使用任务用量与模型元数据确认缓存口径；不确定时跳过并警告 |
| Roo Code | 自动：编辑器 `User/globalStorage/rooveterinaryinc.roo-cline/**/ui_messages.json` | 已规范化输入直接使用；缺少协议／包含关系标记的旧缓存记录跳过并警告 |
| Cursor | **手动导入官方 Usage CSV，默认停用** | 选择导出的 CSV 并启用；需定期更新，不自动登录或获取账号用量 |

`~` 指运行机器当前用户的主目录。Cline / Roo 检查 Code、Code - Insiders、VSCodium、Cursor、Windsurf 的常见扩展目录。macOS 编辑器数据在 `~/Library/Application Support/` 下，Windows 在 `%APPDATA%` 下。便携版或迁移后的路径可在「数据源与设置」修改。

Claude、Codex 的默认根支持 `CLAUDE_CONFIG_DIR`、`CODEX_HOME`。agy 的 `TOKENLOG_AGY_ROOTS` 支持多个根，使用系统路径列表分隔符（macOS 冒号，Windows 分号）。已保存的明确路径可在应用内编辑。不要把同一批调用同时导入两个独立来源，否则会分别作为两个 harness 的记录统计。

## 计数与去重

统一输入包含缓存读取和写入，输出包含推理；这些子计数不能再次加入总计。每条记录合计为统一输入加统一输出。API 原始字段含义不同，采集器按已知格式转换。

Claude Code 合并同一消息的流式片段，Codex 优先使用响应 ID，避免累计快照和子 Agent 镜像重复计入。其他来源也尽量使用稳定调用／消息 ID。导出行或旧累计增量可能合并多次调用，记录条数不保证等于真实请求次数。参考 [OpenAI 日志位置](https://learn.chatgpt.com/docs/reference/troubleshooting) 和 [官方 JSON usage 示例](https://learn.chatgpt.com/docs/non-interactive-mode)。

OpenCode 新版普通输入、缓存读取、缓存写入、可见输出、推理互不重叠。旧版输出包含关系曾变化，通过 `tokens.total` 校验；无法确认的旧推理记录跳过，分叉复制的历史不重复记为新用量。依据：[当前 TokenUsage](https://github.com/anomalyco/opencode/blob/dev/packages/schema/src/token-usage.ts)、[SQLite schema](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/sql.ts)、[旧版实现](https://github.com/anomalyco/opencode/blob/v1.2.25/packages/opencode/src/session/index.ts)。

Gemini CLI 输入为 prompt 加 tool-use prompt，输出为 candidates 加 thoughts；cached 已包含在 prompt 中。重复快照按 ID 合并，rewind 不撤销已经消耗的 token。依据：[CLI 保存结构](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingTypes.ts)、[CLI 写入逻辑](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)、[API UsageMetadata](https://ai.google.dev/api/generate-content#usageMetadata)。

Cline / Roo 不同版本和 API provider 使用过不同缓存口径，只有元数据可确认包含关系时才合并，不确定则警告。Cursor 缺少调用 ID 时按时间、模型、四类 token 和重复行序号去重，同毫秒相同记录的跨导出身份仍有限制；没有会话 ID 时归入导出分组，该分组数不代表真实会话数。

## 已知历史缺口

- 某些 OpenCode 会话累计数可能大于逐条消息用量。程序提示差额；缺少逐条时间／模型时不伪造分配，历史覆盖可能不完整。
- 部分 Gemini CLI 历史会话没有可用 usage，不能由文件存在或对话内容补算。
- 工具可能清理历史、关闭日志，或不保存失败／重试调用的 usage。账本保留已经采集的历史，无法恢复从未取得的记录。
- 这些缺口与订阅额度、API 账单不同；未知用量不会冒充已确认的完整零用量。

## 其他 Harness 的通用导入

在「数据源与设置」添加自定义来源，选择具体文件或目录及格式：

| 格式 | 需要的结构化记录 |
| --- | --- |
| OpenAI / 兼容 API | Chat Completions 或 Responses 的 `usage`，含输入／输出及可用缓存、推理明细 |
| Anthropic | `usage` 中普通输入、缓存读取／写入和输出；按该 API 的独立分项语义处理 |
| Gemini GenerateContent | `usageMetadata`，支持 prompt、tool-use prompt、candidates、thoughts、cached；不同语义的 Live 格式不套用相同统计 |
| Token Ledger 标准记录 v1 | `schema: "tokenlog.usage.v1"`、稳定 `event_id`、`session_id`、带时区 `timestamp` 和 `usage` |

记录须有真实用量、稳定调用身份和有效时间／会话。缺少模型或项目可归入 `unknown`；缺少 usage、ID、时区时间，或总计与分项不一致，会提示并跳过。JSONL 每行一个对象；JSON 可含单条、对象数组或 `events` 数组。

[两条合成示例](../examples/tokenlog-usage.sample.jsonl) 可直接以「Token Ledger 标准记录 v1」导入，共 1,700 token，仅用于验证界面和采集器，与真实账号或模型调用无关。

## agy 会话身份

Antigravity 将子代理写入独立数据库。元数据存在 `root_conversation_id` 时，主会话和子代理按该根 ID 显示为一个会话；仍按稳定响应 ID 去重，模型统计与逐次价格分别保留。缺少明确根 ID 的历史库保留自身会话 ID，不根据相同项目、模型或时间猜测合并。已有账本在重新扫描可用日志时更新，日志已移走时仍保留历史用量。
