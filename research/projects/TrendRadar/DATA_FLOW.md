# TrendRadar 真实数据流

项目：TrendRadar；提交：`8ee26026ba6c11dec41a95fb3895a7162876caa1`；来源：`OFFICIAL_ARCHIVE_SNAPSHOT`。

## 主批处理链

```text
YAML/环境变量
→ load_config
→ NewsAnalyzer + AppContext + StorageManager
→ NewsNow API 热榜抓取 / RSS 抓取
→ NewsData / RSSData 标准模型
→ 按日 SQLite 持久化（可选 TXT、远程 S3 SQLite）
→ Scheduler 解析报告/分析/推送策略
→ 历史合并 + 新增识别
→ 关键词规则 或 AI 标签分类
→ 权重排序/模式裁剪
→ 可选 AI 分析/翻译
→ HTML 报告
→ 多渠道通知
```

### 1. 配置与上下文

- `trendradar/core/loader.py::load_config` 合并 YAML 与受支持的环境变量。
- `trendradar/__main__.py::NewsAnalyzer.__init__` 创建 `AppContext`、`DataFetcher`、`StorageManager`。
- `trendradar/context.py::AppContext` 延迟提供存储、Scheduler、AI Filter 与 NotificationDispatcher。
- 验证：`SOURCE_VERIFIED`。

### 2. 热榜采集

- `NewsAnalyzer._crawl_data` 从 `config['PLATFORMS']` 组装平台 ID 与 `expected_domain`。
- `DataFetcher.fetch_data` GET `{api_url}?id={id}&latest`，检查 JSON `status`。
- `DataFetcher.crawl_websites` 校验返回链接是 HTTPS 且域名匹配，按标题归并多个 rank。
- `convert_crawl_results_to_news_data` 转成 `NewsData/NewsItem`。
- `StorageManager.save_news_data` 转交本地或远程 SQLite 后端。
- 验证：`SOURCE_VERIFIED`，未联网验证 API 响应。

### 3. RSS 采集

- `NewsAnalyzer._crawl_rss_data` 从配置创建 `RSSFeedConfig`。
- `RSSFetcher.fetch_all/fetch_feed` 逐源请求。
- `RSSParser.parse` 区分 JSON Feed 与 feedparser 处理的 RSS/Atom，输出 `ParsedRSSItem`。
- 转成 `RSSItem/RSSData` 后由 `StorageManager.save_rss_data` 写独立 RSS 日库。
- 新鲜度过滤在展示/推送阶段，不阻止旧条目入库（`RSSFetcher.fetch_feed:129-130`）。
- 验证：`SOURCE_VERIFIED`。

### 4. 去重与历史

- 新闻：`SQLiteStorageMixin._save_news_data_impl` 规范化 URL，以 URL + platform 查重；记录 `title_changes`、`rank_history`，缺 URL 时不去重。
- RSS：`_save_rss_data_impl` 优先 GUID + feed，其次 URL + feed；数据库唯一索引同时覆盖二者。
- 新增新闻：`_detect_new_titles_impl` 按平台比较早于当前 crawl_time 的历史标题。
- 新增 RSS：`_detect_new_rss_items_impl` 按 feed 比较 URL。
- 验证：`SOURCE_VERIFIED`。

### 5. 调度与模式

- `NewsAnalyzer._execute_mode_strategy` 调用 `Scheduler.resolve`，得到 collect/analyze/push、report_mode、filter_method、once 等。
- daily/current 会从当日日库加载累计/当前数据；incremental 使用当前抓取结果。
- **时序缺陷**：`NewsAnalyzer.run:1617-1627` 先完成 `_crawl_data` 和 `_crawl_rss_data`，之后才进入 `_execute_mode_strategy:1402-1427` 检查 `schedule.collect`。因此 `collect=false` 不能阻止网络采集或数据库保存，仅停止后续分析。
- 验证：`SOURCE_VERIFIED`。

### 6. 筛选、排序与 AI

- keyword：`core/frequency.py::load_frequency_words/matches_word_groups` 解析并匹配规则；`core/analyzer.py::count_word_frequency` 统计；`calculate_news_weight` 按排名/频次/高位次数加权。
- ai：`AIFilterPipeline.run` 从兴趣文件生成/更新标签，收集未分析新闻，批量调用 `AIFilter.classify_batch`，将标签和结果写回 SQLite。
- AI 分析：`NewsAnalyzer._run_ai_analysis` → `AIAnalyzer.analyze` → `AIClient.chat` → LiteLLM `completion`。
- AI 翻译：在 HTML 与通知前由 `NotificationDispatcher.translate_content` 调用 `AITranslator`。
- 验证：`SOURCE_VERIFIED`；外部模型未调用。

### 7. 报告与通知

- `report/generator.py::generate_html_report` 生成 `output/html/{date}/{time}.html`、`output/html/latest/{mode}.html`、`output/index.html` 与根 `index.html`。
- `NotificationDispatcher.dispatch_all` 固定分派到飞书、钉钉、企业微信、Telegram、ntfy、Bark、Slack、通用 Webhook、Email。
- `notification/splitter.py` 与 `batch.py` 处理渠道字节限制和分片。
- 验证：`SOURCE_VERIFIED`；未生成报告或发送通知。

## MCP 数据流

```text
MCP client
→ FastMCP resource/tool
→ DataService / ParserService / AnalyticsTools / SearchTools
→ output/news/*.db + output/rss/*.db
→ JSON

变更路径：
MCP trigger_crawl → DataFetcher → LocalStorageBackend → SQLite
MCP sync_from_remote → S3 compatible storage → local output
MCP read_article → Jina Reader → Markdown
MCP send_notification → configured external channel
```

注意：`trigger_crawl(save_to_local=False)` 仍调用 `LocalStorageBackend.save_news_data`；源码文案“临时”不能理解为无写入。状态：`SOURCE_VERIFIED`。

