# TrendRadar 可复用能力清单

统一来源：`sansan0/TrendRadar`；提交：`8ee26026ba6c11dec41a95fb3895a7162876caa1`；快照分类：`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不算完成克隆；根许可证：GPL-3.0；复用结论不是法律意见。

## 1. 关键词规则 DSL

```text
能力名称：财经关注词规则与过滤
源码位置：trendradar/core/frequency.py
关键类或函数：load_frequency_words, matches_word_groups, _parse_word
解决的问题：用可编辑文本表达必须词、任意词、排除词、正则、别名和上限
依赖条件：Python 标准库；规则文件
许可证：GPL-3.0
推荐复用方式：REWRITE_FROM_PATTERN
复用难度：中
是否建议采用：是，重写语法/测试，不直接复制源码
验证状态：SOURCE_VERIFIED
```

## 2. 热榜 API 适配与域名防护

```text
能力名称：NewsNow 兼容热榜适配器
源码位置：trendradar/crawler/fetcher.py
关键类或函数：DataFetcher.fetch_data, crawl_websites, _check_domain_safety
解决的问题：批量拉取热榜，并拒绝非 HTTPS/错误域名链接
依赖条件：requests；可信或自部署 NewsNow 兼容 API
许可证：GPL-3.0
推荐复用方式：ADAPTER
复用难度：低
是否建议采用：有条件；优先独立适配，不把公共 API 当可信证据源
验证状态：SOURCE_VERIFIED
```

## 3. RSS 多格式入口

```text
能力名称：RSS/Atom/JSON Feed 标准化
源码位置：trendradar/crawler/rss/parser.py, fetcher.py
关键类或函数：RSSParser.parse, RSSFetcher.fetch_all
解决的问题：把多种 feed 转为统一 RSSItem
依赖条件：requests, feedparser
许可证：TrendRadar GPL-3.0；第三方依赖许可证另查
推荐复用方式：REWRITE_FROM_PATTERN
复用难度：低-中
是否建议采用：是；补 SSRF/大小限制/内容证据归档
验证状态：SOURCE_VERIFIED
```

## 4. 日库去重与排名历史

```text
能力名称：热点 URL 去重、标题变更和排名轨迹
源码位置：trendradar/storage/sqlite_mixin.py, schema.sql
关键类或函数：_save_news_data_impl, _detect_new_titles_impl
解决的问题：合并重复榜单条目，保留标题/排名演化和脱榜点
依赖条件：SQLite；规范化 URL
许可证：GPL-3.0
推荐复用方式：DESIGN_REFERENCE
复用难度：高
是否建议采用：采用模式，不采用按日库作为最终长期数据库
验证状态：SOURCE_VERIFIED
```

## 5. AI 兴趣筛选流水线

```text
能力名称：兴趣描述→标签版本→增量批分类
源码位置：trendradar/ai/filter.py, filter_pipeline.py, storage/ai_filter_schema.sql
关键类或函数：AIFilterPipeline.run, AIFilter.extract_tags, classify_batch
解决的问题：用自然语言兴趣管理标签，并避免重复向模型发送已分析新闻
依赖条件：LiteLLM、API key、SQLite、提示词
许可证：GPL-3.0
推荐复用方式：DESIGN_REFERENCE
复用难度：高
是否建议采用：是，但需重写并增加可解释规则、token/隐私预算和提示注入防护
验证状态：SOURCE_VERIFIED
```

## 6. 时间线调度器

```text
能力名称：周计划/时段/once 动作调度
源码位置：trendradar/core/scheduler.py, config/timeline.yaml
关键类或函数：Scheduler.resolve, already_executed, record_execution
解决的问题：按时段切换报告、筛选、分析和推送策略
依赖条件：StorageBackend 的 period execution API
许可证：GPL-3.0
推荐复用方式：REWRITE_FROM_PATTERN
复用难度：中
是否建议采用：有条件；先修主程序在抓取后才判断 collect 的时序
验证状态：SOURCE_VERIFIED
```

## 7. 多渠道通知编排

```text
能力名称：多账号、多格式、自动分片推送
源码位置：trendradar/notification/dispatcher.py, senders.py, splitter.py, batch.py
关键类或函数：NotificationDispatcher.dispatch_all, split_content_into_batches
解决的问题：把同一报告适配九类渠道及字节限制
依赖条件：各渠道 token/webhook/SMTP
许可证：GPL-3.0
推荐复用方式：SIDE_CAR_SERVICE
复用难度：中-高
是否建议采用：可作为独立 GPL 服务评估，不直接嵌入桌面核心
验证状态：SOURCE_VERIFIED
```

## 8. MCP 查询与分析面

```text
能力名称：新闻库 MCP 工具集
源码位置：mcp_server/server.py, services/, tools/
关键类或函数：run_server；DataQueryTools；AnalyticsTools；SearchTools
解决的问题：向 AI 客户端暴露日期、查询、搜索、趋势、聚合和摘要工具
依赖条件：FastMCP、output SQLite；HTTP 模式需安全网关
许可证：GPL-3.0
推荐复用方式：SIDE_CAR_SERVICE
复用难度：中
是否建议采用：有条件；默认只开放 stdio，HTTP 必须鉴权并收窄变更型工具
验证状态：SOURCE_VERIFIED
```

## 9. LiteLLM 薄适配

```text
能力名称：多模型统一调用
源码位置：trendradar/ai/client.py
关键类或函数：AIClient.chat, validate_config
解决的问题：统一 model/key/base/retry/fallback 参数
依赖条件：litellm
许可证：TrendRadar 薄封装 GPL-3.0；LiteLLM 许可证另查
推荐复用方式：LIBRARY_DEPENDENCY
复用难度：低
是否建议采用：直接评估 LiteLLM 依赖，不复制该薄封装
验证状态：SOURCE_VERIFIED
```
