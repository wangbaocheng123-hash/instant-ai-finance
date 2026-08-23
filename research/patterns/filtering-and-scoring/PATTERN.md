# 筛选与评分模式

解决问题：从大量条目中选择真正与 AI 产业链、紫金矿业、黄金、铜/有色相关且影响重大的信息，并能解释为什么保留或推送。

TrendRadar 的 `frequency.py`/`core/analyzer.py` 提供必须词、任意词、排除词、正则、别名和权重，是六仓最接近目标的确定性实现；其 AI filter 还能版本化兴趣标签。changedetection 提供 selector/ignore/trigger/conditions，适合单页面降噪；n8n IF/Switch/AI 可编排但无财经默认规则；Folo Action 执行端在仓库外；OpenBB 标准模型不是重要度评分；RSSHub 不负责筛选。

推荐 `REWRITE_FROM_PATTERN`：独立设计版本化规则 DSL，评分拆成来源可信度、主题相关度、事件影响、信息新颖度、证据完整度；AI 只作为补充特征。依赖是实体词典、事件 taxonomy、标注集和审计日志。

许可证影响：不复制 TrendRadar GPL 或 n8n SUL 具体实现。建议接口：`score(canonical_item, rule_set_version) -> ScoreBreakdown`，返回每个子分、命中规则、否决原因和推送阈值。
