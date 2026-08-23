# changedetection.io 数据库与存储

## 结论

该提交没有关系数据库或嵌入式 SQL 数据库。正式存储是以 datastore 根目录为边界的 JSON + 索引文本 + 内容/图像二进制文件。它适合单机 sidecar 和直接备份，但不适合作为“即时 AI”的统一财经研究数据库。

## 实际目录模型

```text
<datastore>/
  changedetection.json              # 全局设置、app_guid、版本；不再内嵌 watches/tags
  changedetection-<version>.json    # 版本升级前备份（按条件创建）
  proxies.json                      # 可选代理列表
  headers.txt                       # 可选全局 headers
  secret.txt                        # Flask session secret
  <watch-uuid>/
    watch.json                      # watch 配置/状态
    history.txt                     # 默认文本 processor 历史索引
    history-<processor>.txt         # 非默认 processor 历史索引
    <checksum>.txt[.br]             # 文本快照，可 Brotli
    <checksum>.<binary-ext>          # PDF/图像/二进制快照
    last-checksum.txt                # 原始抓取 checksum，跳过重复处理
    last-screenshot.png             # 可选截图
    elements.deflate                 # 可选 XPath 元数据
    <processor>.json                 # processor 私有配置
    change-summary-*.txt             # LLM 摘要缓存
    favicon.* / thumbnail.jpeg / error artifacts / headers.txt
  <tag-uuid>/
    tag.json
  temporary/<temp-uuid>/             # Add Watch 临时快照
```

## 设置与实体保存

- `ChangeDetectionStore._build_settings_data` 将 tag 清空后保存 `changedetection.json`，明确注记 watch/tag 位于独立 UUID 目录。
- `EntityPersistenceMixin._save_to_disk` 根据实体类型保存 `watch.json` 或 `tag.json`。
- `save_json_atomic` 在同目录创建 temp 文件，序列化后 `os.replace`；文件 fsync 默认关闭，`FORCE_FSYNC_DATA_IS_CRITICAL=true` 可开启。
- 源码明确调用方需持 datastore lock，且不支持同 datastore 多进程并发。

## Watch 数据模型

`model/__init__.py::watch_base` 是继承 `dict` 的兼容模型，核心字段包括：

- 身份/来源：`uuid`、`url`、`title`、`page_title`、`tags`；
- 抓取：`fetch_backend`、`method`、`headers`、`proxy`、browser steps、JS；
- 调度：`time_between_check`、`time_schedule_limit`、`paused`；
- 过滤：include/subtractive selector、ignore/trigger/extract text、conditions；
- 差异：`previous_md5`、processor、unique lines、added/removed/replaced；
- 状态：`last_checked`、`check_count`、`last_error`、`fetch_time`；
- 通知：URL、title/body/format、muted、screenshot；
- LLM：intent、summary prompt、预算计数/缓存相关字段。

模型没有财经文章、发布者、证券代码、实体、事件、行情 K 线或跨来源 canonical item 表。

## 新闻/原文和链接

- 原始链接：保存在 `watch.json` 的 `url` 字段，`Watch.link` 会进行 Jinja 渲染和 URL 再验证。
- 内容：只有首次或检测到变化时保存处理后的 `contents` 历史快照；worker 还可保存 last fetched HTML，具体是否为未经处理的完整原文取决于 fetcher/processor。
- 因此它能保存网页变化证据，但不能等同于规范化新闻原文仓；页面抓取的授权、robots/条款和版权仍需由使用方负责。

## 去重能力

源码确认四类“去重/抑噪”：

1. 同一 UUID 的 running/queued 防重复；
2. 原始文档 checksum + filter hash 不变时跳过处理；
3. 过滤后 MD5 与 `previous_md5` 比较；
4. `check_unique_lines` 与该 watch 的历史行集合比较。

未发现跨 watch、跨 URL、跨媒体的 URL canonicalization、SimHash/embedding 聚类或财经事件合并。因此“支持新闻去重”的表述不成立。

## 历史查询与迁移

- `Watch.history` 从 `history*.txt` 重建 timestamp→snapshot path；REST API 和 UI 可读单快照与任意两版本 diff。
- `history_snapshot_max_length` 可裁剪旧快照。
- `ChangeDetectionStore.run_updates` 支持 schema migration，并能从旧 `url-watches.json` 迁移。
- 文件可直接备份/移动，适合单机恢复；但缺少事务、索引、查询语言和多进程并发，迁移到 SQL 需要显式 ETL。
- 源码注释设想 Redis/SQL 多态方法，但仓库内 `FileSavingDataStore` 只是抽象文档中的未来示例，未找到可运行 SQL/Redis backend；不得声称已支持。

## Windows 与 H 盘适配

- `changedetectionio/__init__.py::main` 在 Windows 默认使用 `%APPDATA%\changedetection.io`，也允许 `-d PATH` 指定任意 datastore。
- 因而理论上可把 sidecar 数据目录指向 `H:\即时AI文件库` 下未来经 ADR 批准的隔离子目录；**本轮未创建目录、未写业务数据、未运行验证。**
- 建议保持 changedetection 自己的 datastore 与“即时 AI”数据库分离，只把其 API/RSS/事件转换为“即时 AI”证据对象。

## 关键证据

| 源码路径 | 类/函数/配置 | 结论 | 状态 |
|---|---|---|---|
| `changedetectionio/store/__init__.py` | `ChangeDetectionStore._build_settings_data/_save_settings/_load_watches` | settings、watch、tag 分文件保存 | `SOURCE_VERIFIED` |
| `changedetectionio/store/file_saving_datastore.py` | `save_json_atomic` | temp+replace 原子 JSON；单进程限制 | `SOURCE_VERIFIED` |
| `changedetectionio/model/persistence.py` | `EntityPersistenceMixin._save_to_disk` | watch/tag 实体文件命名与大小上限 | `SOURCE_VERIFIED` |
| `changedetectionio/model/Watch.py` | `history/get_history_snapshot/save_history_blob` | 历史索引、路径限制、Brotli/二进制快照 | `SOURCE_VERIFIED` |
| `changedetectionio/processors/text_json_diff/processor.py` | `ChecksumCalculator`、`run_changedetection` | 单 watch 内容指纹判变 | `SOURCE_VERIFIED` |

项目：`dgtlmoon/changedetection.io`；提交：`fce24780e74199bf34c62a0d90188cc2fc12f061`。

