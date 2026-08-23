# AI 分析模式

解决问题：在确定性采集和规则之后完成分类、摘要、翻译和研究辅助，同时控制隐私、成本、提示注入与幻觉。

TrendRadar 通过 LiteLLM 提供分析、翻译和兴趣标签分类，并记录已分析新闻；changedetection 只在确认发生变化后调用 LiteLLM evaluator/summary，且有输入和月度预算；n8n 的模型/Agent/tool/vector/MCP 节点最广但需要流程作者治理；Folo 有成熟 AI 界面和远端调用点，执行后端不可审计；OpenBB `to_llm()` 只是序列化；RSSHub 无 AI。

最佳业务模式是“changedetection 的变化后调用 + TrendRadar 的标签版本/增量记录”，并借鉴 n8n 的 Provider 可插拔性，但全部独立实现。推荐 `REWRITE_FROM_PATTERN` 或直接采用许可证合适的模型 SDK，不复制上游 glue code。

建议接口：`analyze(evidence_ids, task, template_version, budget) -> AIResult`。输出必须记录模型、输入 hash、证据引用、时间、成本、错误和人工状态；AI 失败不得阻断入库。任何工具调用默认关闭，内容视为不可信输入。
