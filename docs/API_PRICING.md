# API 对照价值

本版本的价目快照核验于 **2026-11-01**，币种 **USD**，单位为 **每百万 token**。使用 Standard、全局路由、付费文本 API 价目；所有历史记录统一按该快照估值。金额不是订阅实付、节省金额或历史账单复算。

## 分项公式

```text
普通输入 = 输入总数 - 缓存读取 - 缓存写入
API 等价值 = (普通输入 × 普通输入价
             + 缓存读取 × 读取价
             + 短时缓存写入 × 短时写入价
             + 长时缓存写入 × 长时写入价
             + 输出 × 输出价) / 1,000,000
```

输出已经包含推理 token；“其中推理”只展示组成，不再加价一次。缓存写入按写入单价替代普通输入单价，不是先算一遍普通输入再加一遍完整写入费。

Claude 的短时/长时字段为 5 分钟/1 小时；OpenAI 当前支持的模型为 30 分钟/12 小时。记录保留了具体时长时按各自数量计算；缺时长的写入部分按两个单价的最小和最大值显示区间。

例如 Claude Opus 5.5 的普通输入为 $4/百万、缓存读取 $0.20/百万、5 分钟写入 $5/百万、1 小时写入 $8/百万、输出 $20/百万。写入 100 万 token 按 5 分钟或 1 小时规则分别是 $5 或 $8，不是 $4+$5 或 $4+$8。

## 长上下文、未知模型与不包含的费用

- GPT-6 系列的已核验模型对超过 272,000 输入 token 的单次请求使用长上下文档位；Claude Haiku 5.5 的门槛为 100,000 输入 token。输入长度包含缓存。程序逐次计算，再汇总日期、模型和会话；不会把会话总 token 错当成单次上下文长度。
- 旧版累计增量可能合并多次调用，无法确定上下文档位时显示区间，并标明其不是已确认的单次调用。
- 代理后缀、内部审查模型、未知及本地模型没有可靠公开单价时标为未定价，不按相似名称猜价格，不当成 $0。“API 单价”可填写自定义固定价格，来源明确标为用户自定义。
- Gemini 的部分模型有显式缓存存储时长费用；用量日志没有缓存持有时长，估值不包含这项费用。工具调用、搜索、税、地区/服务等级溢价、音频/图片等模态差价也不包含。
- 当前 Gemini 促销价随日期变化，本程序保留并显示核验日期，不暗中把旧快照称为实时价格。

## 官方来源

- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [GPT-6.1 Sol 价格与长上下文规则](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [GPT-6.1 Terra 价格与长上下文规则](https://developers.openai.com/api/docs/models/gpt-6.1-terra)
- [GPT-6 Luna 价格与长上下文规则](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [GPT-6 Sol 价格与长上下文规则](https://developers.openai.com/api/docs/models/gpt-6-sol)
- [Anthropic 模型、缓存与长上下文定价](https://platform.claude.com/docs/en/about-claude/pricing)
- [Google Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)

## 界面与导出

“逐次调用与估值”支持按模型/调用 ID/时间/项目搜索和翻页；双击查看完整 token、单价和金额拆分。CSV 导出当前筛选的所有记录，追加 USD 低/高估值、价目日期及说明。未定价记录的金额留空，并在汇总中报告未计入数量。

自定义价格保存在用户数据目录 `price-overrides.json`，不含对话或凭据；价格文件不会打入发行包。
