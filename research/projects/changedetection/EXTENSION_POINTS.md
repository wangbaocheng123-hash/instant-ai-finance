# changedetection.io 扩展点

## Pluggy 主插件机制

`changedetectionio/pluggy_interface.py` 建立 namespace `changedetectionio`，加载内置目录和 setuptools entry points。`ChangeDetectionSpec` 的源码 hooks 包括：

- `register_content_fetcher`：注册 `Fetcher` 子类；
- `register_processor`：注册外部 processor 模块/`perform_site_check`；
- `update_handler_alter`：抓取/判变前包装 handler；
- `update_finalize`：处理完成后的 cleanup/metrics；
- `get_itemprop_availability_override`：补充库存/价格提取；
- `plugin_settings_tab`、template/static/head/UI extras：扩展设置和 UI；
- fetcher status icon 等 UI metadata。

外部包发现调用 `plugin_manager.load_setuptools_entrypoints('changedetectionio')`。容器 `docker-entrypoint.sh` 支持 `EXTRA_PACKAGES`，但它每次启动执行 pip；在正式产品中不应允许用户输入未经审查的包名。

验证：`SOURCE_VERIFIED`。

## Processor 机制

- `processors/__init__.py::find_processors` 扫描内置 `processors.<name>.processor`，并合并 Pluggy `register_processor` 结果。
- `get_processor_module` 返回含 `perform_site_check` 的实际模块。
- processor 可带 `api.yaml`，`api/__init__.py::build_merged_spec_dict` 合并 schema 和 code samples。
- processor 配置使用 `{uuid}/<processor>.json`。

这是扩展新的变化算法/提取逻辑的正式点，但 processor 与 Watch/Datastore/Fetcher 生命周期仍有耦合。

验证：`SOURCE_VERIFIED`。

## Fetcher/Adapter 机制

- `content_fetchers.resolve_content_fetcher` 统一解析 watch→global backend；
- built-in requests/browser fetcher 也通过插件注册对象暴露；
- 外部 fetcher 必须继承 `content_fetchers.base.Fetcher` 并声明 capabilities；
- proxy、custom browser endpoint 和 browser steps 是现成 adapter 配置。

验证：`SOURCE_VERIFIED`。

## Conditions 插件

`conditions/pluggy_interface.py` 使用第二 namespace `changedetectionio_conditions`，支持注册 JSON Logic operators、operator choices、field choices 和额外数据，并加载 setuptools entry points。

验证：`SOURCE_VERIFIED`。

## REST API

API 是“即时 AI”首选集成点：watch/tag CRUD、import、search、history、single snapshot、diff、notifications test/system info，使用 `x-api-key`。OpenAPI 来自 `docs/api-spec.yaml`。

推荐方式：`API_INTEGRATION`，而非从内部 Python 模块 import。

## RSS

- `/rss?token=...`
- `/rss/watch/<uuid>?token=...`
- `/rss/tag/<uuid>?token=...`

`blueprint/rss/_util.py::validate_rss_token` 验证独立 RSS token。RSS 适合低耦合消费变化，但事件字段和幂等键需要“即时 AI”适配层补充。

## 出站通知 / Webhook

Apprise notification URL 可覆盖 email/IM 以及 HTTP/JSON 类自定义通知；`notification/handler.py::process_notification` 和 `notification/apprise_plugin/custom_handlers.py` 承担发送。因此可把“变化发生”推向“即时 AI”的本地 HTTP adapter。

未找到独立、通用的**入站 webhook** endpoint 或 webhook subscription registry。README 中有关 webhook 的表述若有，只能理解为出站 notification URL；该结论已由源码搜索确认。

## CLI

`changedetection.io` console script 支持添加 URL、批量 recheck 和 batch mode。可由 Windows Task Scheduler 外部驱动，但常规 app 已有进程内 ticker。

## 数据库接口

`store/base.py::DataStore` 与 `FileSavingDataStore` 提供抽象方法外观；注释列出 Redis/SQL 的可能实现，但快照内未找到正式 Redis/SQL backend。当前 `ChangeDetectionStore` 是文件实现，不能宣称数据库后端可插拔已经完成。

验证：文件 backend `SOURCE_VERIFIED`；Redis/SQL 支持为 `UNVERIFIED/NOT_IMPLEMENTED_IN_SNAPSHOT`。

## MCP 与自定义节点

- MCP：全快照静态搜索无匹配，未定位 MCP server/client/tool。
- n8n 类自定义节点：没有独立节点 SDK；可用 REST、RSS 或 HTTP notification 与外部工作流系统连接。

## 对“即时 AI”的优先顺序

1. `API_INTEGRATION`：控制 watch、拉取历史/diff，最可审计；
2. 出站 HTTP notification：低延迟事件触发，随后回 API 拉证据；
3. RSS adapter：低成本轮询备选；
4. 外部 Pluggy processor/fetcher：只在 API 无法满足且插件许可证/部署获得批准时使用；
5. 内部模块 import 或源码混合：拒绝。

项目/提交：`dgtlmoon/changedetection.io` / `fce24780e74199bf34c62a0d90188cc2fc12f061`。

