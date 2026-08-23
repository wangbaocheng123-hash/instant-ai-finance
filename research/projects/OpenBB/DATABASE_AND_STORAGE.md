# OpenBB 数据库与存储

研究锚点：`OpenBB-finance/OpenBB`，提交 `3e071fcc2cd9f891cac6040ae60296dba76dab46`，`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`。

## 总结

OpenBB 快照没有中心业务数据库。查询结果默认驻留在 `OBBject` 内存对象中；持久化主要分为用户设置、日志、导出目录、Workspace apps 配置、MCP 配置以及 provider 自己的 HTTP/CSV/SQLite 缓存。这一结构适合“按需取数”，不适合直接承担“即时 AI”的新闻原文、证据快照、去重索引、事件历史和长期研究库。

## 默认目录与文件

| 数据 | 上游默认位置 | 源码证据 | 说明 |
|---|---|---|---|
| 用户设置/凭据 | `~/.openbb_platform/user_settings.json` | `core/openbb_core/app/constants.py::USER_SETTINGS_PATH`；`UserService.write_to_file` | JSON 明文落盘 |
| 系统设置 | `~/.openbb_platform/system_settings.json` | `constants.py`、`SystemSettings.create_openbb_directory` | JSON |
| 环境变量 | `~/.openbb_platform/.env` | `core/openbb_core/env.py::Env.__init__` | dotenv |
| MCP 设置 | `~/.openbb_platform/mcp_settings.json` | `mcp_server/service/mcp_service.py::MCP_SETTINGS_PATH` | JSON |
| 数据根 | `~/OpenBBUserData` | `core/openbb_core/app/model/preferences.py::Preferences.data_directory` | 可配置 |
| 缓存 | `~/OpenBBUserData/cache` | `Preferences.cache_directory`、`app/utils.py::get_user_cache_directory` | provider 各自使用 |
| 导出 | `~/OpenBBUserData/exports` | `Preferences.export_directory` | CLI routines/图表等使用 |
| 日志 | `<data_directory>/logs` | `app/logs/utils/utils.py::create_log_dir_if_not_exists` | 文件日志 |
| Workspace apps | `<data_directory>/workspace_apps.json` | `platform_api/main.py::APPS_PATH` | JSON |
| 桌面环境配置 | `~/.openbb_platform/environments/openbb.yaml` | `desktop/.../startup.rs::generate_environment_yaml` | Conda YAML |

以上均为 `SOURCE_VERIFIED`。

## 数据模型

- `openbb_platform/core/openbb_core/provider/standard_models/`
  - 以 Pydantic `QueryParams` 和 `Data` 子类定义跨 provider 的标准 schema。
- `openbb_platform/core/openbb_core/app/model/obbject.py::OBBject`
  - 运行结果容器字段：`results`、`provider`、`warnings`、`chart`、`extra`。
- `OBBject.to_dataframe()`、`to_polars()`、`to_numpy()`、`to_dict()`、`to_llm()`
  - 都是转换函数，不会自动写数据库。

验证：`SOURCE_VERIFIED`。

## 新闻保存、链接和去重

- `standard_models/company_news.py::CompanyNewsData` 包含 `date/title/author/excerpt/body/images/url/symbols`。
- `standard_models/world_news.py::WorldNewsData` 包含同类字段，URL 可为空。
- FMP 和 yfinance news Fetcher 会把供应商字段标准化并保留 article URL。
- 未发现新闻查询完成后自动写本地文件/数据库的调用。
- 未发现基于 URL、标题、正文指纹或内容哈希的新闻去重表和唯一索引。

结论：**保留来源链接：支持；自动保存新闻与原文：不支持；新闻去重：未发现。** 状态：`SOURCE_VERIFIED`。

## Provider 局部缓存

局部缓存不能等同于业务数据库：

- `openbb_platform/providers/finra/openbb_finra/utils/data_storage.py`
  - `DB_PATH = <cache>/caches/finra_short_volume.db`，通过 sqlite3/pandas `to_sql` 保存 FINRA 数据。
- `openbb_platform/providers/sec/openbb_sec/utils/form4.py`
  - 在 `<cache>/sql` 建 SQLite 缓存。
- SEC、TMX、EconDB、ECB、CBOE 等代码调用 `get_user_cache_directory()` 创建 HTTP cache。
- OECD helper 会把数据写 CSV cache。

这些实现按 provider 分散，没有统一迁移、备份、查询或 retention 层。验证：`SOURCE_VERIFIED`。

## 历史查询与迁移

- 历史行情、宏观和新闻日期查询由外部 provider 支持，属于远端数据查询，不是本地历史库。
- OBBject 可转为 dataframe/dict，数据导出和迁移很容易；但 metadata、provider 特有字段和类型需要我们自行定义持久化 schema。
- 没有统一数据库迁移框架、ORM schema 或业务数据库版本表。

结论：**结果导出容易；作为完整历史数据库迁移来源不足。** 状态：`SOURCE_VERIFIED`。

## Windows 与 H 盘适配

`Preferences` 使用普通字符串路径，日志和 provider cache 通过 `pathlib.Path`，理论上可把以下项显式配置到 H 盘：

```json
{
  "preferences": {
    "data_directory": "H:\\即时AI文件库",
    "cache_directory": "H:\\即时AI文件库\\cache\\openbb",
    "export_directory": "H:\\即时AI文件库\\exports\\openbb",
    "user_styles_directory": "H:\\即时AI文件库\\cache\\openbb\\styles"
  }
}
```

这只是基于字段类型和路径调用的静态适配建议，`RUNTIME_UNVERIFIED`。凭据和 OpenBB 自身系统设置默认仍在用户目录；不得把真实密钥写进研究仓库。产品实施时应给 OpenBB 单独子目录，并把真正的 `raw/evidence/database` 交给“即时 AI”自己的存储层。
