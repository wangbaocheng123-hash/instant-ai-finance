# TrendRadar 安全与风险

项目/提交：TrendRadar / `8ee26026ba6c11dec41a95fb3895a7162876caa1`。来源为 `OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不算完成克隆。风险由固定快照静态分析得出；未做动态攻击验证。

## 高风险

### 1. MCP HTTP 默认全网卡且含变更型工具

- 证据：`mcp_server/server.py::run_server` 默认 `host='0.0.0.0'`；`start-http.bat` 同样固定全网卡。
- 能力：`trigger_crawl` 写 SQLite，`sync_from_remote` 下载文件，`read_article` 对外请求，`send_notification` 向外部渠道发消息。
- 当前源码未见 TrendRadar 自身鉴权/授权中间件。
- 影响：局域网或错误端口映射下，未授权调用可能消耗网络/API、写数据或对外发消息。
- 建议：默认 stdio；HTTP 仅 127.0.0.1，外层鉴权，工具 allowlist，通知/抓取工具单独授权。
- 状态：`SOURCE_VERIFIED`；FastMCP 是否另有默认安全机制 `UNVERIFIED`。

### 2. 调度 `collect=false` 不阻止采集

- 证据：`NewsAnalyzer.run:1617-1627` 先 `_crawl_data/_crawl_rss_data`；`_execute_mode_strategy:1402-1427` 后判断 `schedule.collect`。
- 影响：用户认为暂停采集时仍会访问外网和写库，违反最小化与成本预期。
- 建议：把 `Scheduler.resolve` 和 collect 判断移到任何网络/存储之前，并增加回归测试。
- 状态：`SOURCE_VERIFIED`。

### 3. 外部内容进入 AI，存在泄露与提示注入面

- 证据：`AIAnalyzer._prepare_news_content`/`_call_ai` 将新闻标题/RSS 摘要装入 prompt；`AIFilter.classify_batch` 也发送外部标题；`AIClient.chat` 调 LiteLLM 外部 provider。
- 影响：敏感兴趣画像和内容可能离开本机；恶意标题/摘要可能操纵分析输出。
- 建议：明确数据出境提示、provider allowlist、内容与指令分隔、长度/字符清洗、结构化输出验证、禁用模型工具调用、保留审计。
- 状态：代码路径 `SOURCE_VERIFIED`；具体可利用性 `UNVERIFIED`。

## 中风险

### 4. RSS URL 缺少网络目的地限制

`RSSFetcher.fetch_feed` 对配置 URL 直接 `requests.Session.get`，未见私网/回环/重定向目的地检查、响应大小上限或 content-type allowlist。可信本地配置下风险较低；若配置被不可信输入控制则可形成 SSRF/资源耗尽面。`SOURCE_VERIFIED`。

### 5. 文章阅读把 URL 交给第三方 Jina Reader

`ArticleReaderTools.read_article` 请求 `https://r.jina.ai/{原URL}`。这会向第三方披露目标 URL，且文档提到付费墙并不等于具备抓取授权。必须遵守“不绕过付费墙”，对受限站点禁用或仅打开原站。`SOURCE_VERIFIED`。

### 6. 默认热榜 API 是外部信任单点

`DataFetcher.DEFAULT_API_URL` 是公共 NewsNow 实例。虽有平台目标域名/HTTPS校验，但无法证明标题、排名、完整性和时效性；服务可观察查询。建议自部署/替换并记录原始响应哈希。`SOURCE_VERIFIED`。

### 7. 安装与构建供应链

- `setup-windows.bat` 在缺 uv 时执行 `irm https://astral.sh/uv/install.ps1 | iex`，或 pip 安装。
- Docker 使用 `ghcr.io/astral-sh/uv:latest`，未钉镜像 digest。
- GitHub Actions 用 tag 版本 action，而非 commit SHA。

建议走审批、校验发行哈希、固定版本/digest，并生成 SBOM。`SOURCE_VERIFIED`。

### 8. 密钥可写 YAML，日志仍暴露 key 前缀

配置支持把 AI/S3/webhook/邮件凭据写入 YAML；`AIAnalyzer.analyze` 打印 API key 前五位后掩码。生产应仅使用安全凭据注入并完全不打印 key。`SOURCE_VERIFIED`。

### 9. 自定义兴趣文件可能目录穿越

`AIFilter.load_interests_content` 把配置值直接拼接到 `config/custom/ai/filename`，未 resolve 后校验仍在目录内。当前值来自可信配置/时间线，但若配置编辑入口未来对外开放，可读取任意可访问文本文件。建议只接受 basename 并检查 resolved parent。`SOURCE_VERIFIED`。

## 数据质量与运维风险

- 热榜 URL 为空时不去重，可能无限重复插入。
- RSS 持久化优先 GUID，但“新增”只按 URL，语义不一致。
- `RSSItem.to_dict/from_dict` 丢失 guid。
- 按日双库不利于跨日一致性、全文检索和 migration。
- 远程存储上传整个 SQLite；多实例并发覆盖尚未验证。
- 主 `NewsAnalyzer` 和 MCP analytics 存在平行分析实现，可能随版本漂移。
- 快照无测试目录，关键行为无可见自动化保护。

## 积极安全证据

- 热榜 `expected_domain` 使用 `urlparse().hostname`，防 userinfo 绕过，并要求 HTTPS。
- SQLite 使用参数化 SQL。
- MCP 配置查询只返回渠道状态/来源，不返回 secret 值。
- 通知与批量抓取有超时/限量；文章批量最多 5 篇。
- Docker compose 默认把静态 Web 映射到宿主 127.0.0.1，MCP 默认映射也可由 `MCP_HOST` 设为 127.0.0.1。

以上均 `SOURCE_VERIFIED`，但不替代动态安全测试。
