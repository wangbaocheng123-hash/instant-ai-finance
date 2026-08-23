# changedetection.io 真实数据流

## 主链路

```text
URL / RSS / JSON API / PDF / 网页
→ watch 注册（UI / REST / CLI / import）
→ 文件型 datastore 保存 watch.json
→ ticker 或手工/API/CLI 将 UUID 放入 RecheckPriorityQueue
→ worker_pool 分配 async_update_worker
→ processor 发现并实例化 perform_site_check
→ validate_fetch_url_async 做 scheme/SSRF gate
→ resolve_content_fetcher 选择 requests / browser / plugin fetcher
→ fetcher.run 抓取内容、headers、状态码、可选截图/XPath/favicon
→ processor 预处理 RSS/PDF/JSON/HTML
→ CSS/XPath/JSON/减法选择器/文本规则/条件过滤
→ checksum 或专用算法判定 changed_detected
→ 可选 LLM intent 再过滤、按需生成变化摘要
→ update_watch 原子保存 watch 状态
→ Watch.save_history_blob 保存内容快照和 history 索引
→ 若第二次及以后有效变化且未 muted，则 NotificationService 入队
→ notification_runner → Jinja/diff 渲染 → Apprise → 外部渠道/API
→ UI / REST history+diff / RSS / Socket.IO 消费结果
```

## 分阶段调用链证据

### 1. 注册与持久化

- UI/API/CLI 最终调用 `changedetectionio/store/__init__.py::ChangeDetectionStore.add_watch`。
- 新 watch 使用 `changedetectionio/model/Watch.py::model`（基类 `model/__init__.py::watch_base`），URL、processor、fetch backend、过滤、通知和调度都存为 watch 字段。
- `add_watch` 创建 `{datastore}/{uuid}` 并由 `watch.commit()` 保存 `{uuid}/watch.json`。

状态：`SOURCE_VERIFIED`。

### 2. 调度和去重入队

- `flask_app.py::ticker_thread_check_time_launch_checks` 按 `last_checked` 排序，应用 `time_between_check`、`time_schedule_limit`、`jitter_seconds` 和 proxy reuse。
- 它读取 `worker_pool.get_running_uuids()` 和队列 UUID 集合，避免同一 watch 同时运行或重复入队。
- `worker_pool.queue_item_async_safe` 把 `queuedWatchMetaData.PrioritizedItem` 放入 `RecheckPriorityQueue`。

这里的“去重”是**任务/同 watch 并发去重**，不是跨来源财经新闻语义去重。

状态：`SOURCE_VERIFIED`。

### 3. worker 取任务

- `worker_pool.py::WorkerThread.run` 在专用线程内运行 event loop。
- `worker.py::async_update_worker` 调 `q.async_get`，并用 `worker_pool.claim_uuid_for_processing` 原子 claim UUID；重复任务会延迟后重新入队。
- worker 根据 `watch['processor']` 调 `processors.get_processor_module` 并实例化 `perform_site_check`。

状态：`SOURCE_VERIFIED`。

### 4. URL 安全门和抓取

- `processors/base.py::difference_detection_processor.call_browser` 首先调 `validate_url_is_fetchable` → `validate_url.validate_fetch_url_async`。
- `validate_url.py::is_fetch_url_allowed` 默认拒绝 `file://`、反斜杠 parser differential、私网/保留地址和不安全协议；管理员环境变量可放宽其中部分约束。
- `content_fetchers.resolve_content_fetcher` 从 watch→global 解析 backend；PDF 强制 requests；browser steps 可覆盖为 Playwright。
- fetcher 获得合并后的 headers、Jinja 渲染 body、method、proxy、timeout、browser steps 和 JS，再执行异步 `fetcher.run`。

状态：`SOURCE_VERIFIED`。

### 5. 标准化、过滤与差异

默认 `text_json_diff.perform_site_check.run_changedetection`：

1. 计算原始文档 checksum 和 filter-config hash，无变化且 watch 未编辑时提前跳过；
2. `guess_stream_type` 判断 RSS/PDF/JSON/HTML/plaintext；
3. RSS/PDF/JSON 格式化、HTML 混淆处理；
4. 先减法 selector，再 CSS/XPath/JSON include filter；
5. HTML/RSS 转文本；
6. whitespace、ignore text、line/regex extract、去重/排序；
7. trigger text、text-not-present 和条件插件可阻断；
8. `ChecksumCalculator.calculate` 计算 MD5，与 `watch.previous_md5` 比较；
9. `check_unique_lines` 可与全部历史比较，抑制无新行的变化。

该层输出 `(changed_detected, update_obj, stripped_text)`。它是单 watch 内容规范化与判变，不执行跨 watch 内容聚类。

状态：`SOURCE_VERIFIED`。

### 6. AI 可选后处理

- worker 只在 `changed_detected` 且至少存在一份历史时构造 unified diff。
- `llm.evaluator.evaluate_change` 按 watch/tag/global intent 判断 `important`；返回 false 时 worker 将 `changed_detected=False`。
- 只有确实将发送通知时，才按需调 `summarise_change`；结果写入 `change-summary-<from>-to-<to>-<prompt_hash>.txt`。
- 月度/单 watch token 预算可 fail-open（不抑制通知）或按全局策略跳过检查。

状态：`SOURCE_VERIFIED`；任何具体 LLM provider 的成功调用均为 `UNVERIFIED`。

### 7. 状态与证据落盘

- `ChangeDetectionStore.update_watch` 更新内存 watch 并立即 `watch.commit()`。
- 当变化或首次抓取时，worker 调 `Watch.save_history_blob`：文本通常保存为 `<md5>.txt` 或大文件 Brotli `.txt.br`；二进制按检测扩展名保存；`history*.txt` 记录 `timestamp,filename`。
- 还可保存 `last-screenshot.png`、`elements.deflate`、favicon、last fetched HTML、error artifacts 和 LLM summary cache。

状态：`SOURCE_VERIFIED`。

### 8. 通知

- worker 在 `history_n >= 2` 且 watch 未 muted 时调用 `send_content_changed_notification`。
- `NotificationService._check_cascading_vars` 按 watch→tag→global 获取 URL/title/body/format。
- `queue_notification_for_watch` 读取前后快照并放入 `NotificationQueue`。
- `flask_app.notification_runner` 调 `notification.handler.process_notification`；后者生成 diff/token、Jinja 渲染并调用 Apprise。

状态：`SOURCE_VERIFIED`；实际渠道发送为 `UNVERIFIED`。

### 9. 展示和输出

- UI diff：`blueprint/ui/diff.py` 与 processor 的 `difference.py`。
- REST history/diff：`api/Watch.py::WatchHistory*`。
- RSS：`blueprint/rss/main_feed.py`、`single_watch.py`、`tag.py`，均检查 RSS token。
- 实时 UI：`watch_check_update`/Socket.IO。

状态：`SOURCE_VERIFIED`。

## “即时 AI”接入后的建议数据流

```text
changedetection sidecar watch
→ REST/RSS/出站 HTTP 通知取得变化事件与 watch UUID
→ 即时 AI 适配器拉取 before/after snapshot 和原始 URL
→ 写入 H:\即时AI文件库\raw 与 evidence（按未来已批准 schema）
→ 即时 AI 自己做来源身份、内容去重、实体识别、财经事件分类、重要度评分
→ 桌面端展示与低噪声推送
```

R0 阶段只建议接口边界，不创建正式数据库或业务数据。

所有源码证据对应项目 `dgtlmoon/changedetection.io`、提交 `fce24780e74199bf34c62a0d90188cc2fc12f061`。

