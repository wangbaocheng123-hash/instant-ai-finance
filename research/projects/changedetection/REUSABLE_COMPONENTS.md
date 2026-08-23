# changedetection.io 可复用能力清单

## 1. 网页变化监控服务

```text
能力名称：网页变化监控 sidecar
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/flask_app.py；worker.py；processors/；store/
关键类或函数：changedetection_app；ticker_thread_check_time_launch_checks；async_update_worker
解决的问题：权威网页的周期抓取、判变、历史和通知
依赖条件：独立 Python 服务、独立 datastore；JS 页面另需 browser endpoint
许可证：根 LICENSE 为 Apache-2.0；COMMERCIAL_LICENCE.md 的 hosting 声明待澄清
推荐复用方式：API_INTEGRATION
复用难度：中
是否建议采用：是，作为候选 SIDE_CAR_SERVICE，需运行和许可证复核
验证状态：SOURCE_VERIFIED
```

## 2. Watch/Tag/History REST API

```text
能力名称：监控控制与证据读取 API
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/api/；changedetectionio/flask_app.py；docs/api-spec.yaml
关键类或函数：CreateWatch；Watch；WatchHistory；WatchSingleHistory；WatchHistoryDiff；check_token
解决的问题：外部系统创建监控、触发重查、读取快照和差异
依赖条件：服务进程、API token、adapter 的幂等/错误处理
许可证：Apache-2.0 主体；服务边界避免源码混合
推荐复用方式：API_INTEGRATION
复用难度：低
是否建议采用：是
验证状态：SOURCE_VERIFIED
```

## 3. 低噪声文本差异管线

```text
能力名称：选择器、文本规则、条件和 checksum 判变
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/processors/text_json_diff/processor.py
关键类或函数：FilterConfig；ContentProcessor；ChecksumCalculator；perform_site_check.run_changedetection
解决的问题：从动态网页中提取稳定区域并抑制无关变化
依赖条件：Watch/Datastore/html_tools/conditions 强耦合
许可证：Apache-2.0 主体
推荐复用方式：DESIGN_REFERENCE
复用难度：中
是否建议采用：采用模式；不直接复制为主系统代码
验证状态：SOURCE_VERIFIED
```

## 4. 多抓取后端解析

```text
能力名称：requests 与浏览器 fetcher 分层
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/content_fetchers/__init__.py；base.py；requests.py；playwright.py
关键类或函数：resolve_content_fetcher；Fetcher；fetcher.run
解决的问题：低成本静态抓取与 JS/browser steps 抓取按 watch 切换
依赖条件：浏览器 endpoint、proxy、第三方库；安全 gate
许可证：主体 Apache-2.0，第三方依赖另审
推荐复用方式：API_INTEGRATION
复用难度：中高
是否建议采用：作为 sidecar 内建能力使用
验证状态：SOURCE_VERIFIED
```

## 5. 出站通知

```text
能力名称：Apprise 多渠道通知与 HTTP/JSON 事件
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/notification_service.py；notification/handler.py
关键类或函数：NotificationService；process_notification
解决的问题：变化事件模板化、排队并发送至多渠道
依赖条件：Apprise URL、外部凭据和网络；通知内容安全
许可证：主体 Apache-2.0，Apprise 许可证需在总矩阵确认
推荐复用方式：API_INTEGRATION
复用难度：低到中
是否建议采用：可用于 sidecar→即时 AI 事件触发；最终用户推送仍由主系统统一治理
验证状态：SOURCE_VERIFIED
```

## 6. 可选 LLM 变化预筛选

```text
能力名称：LLM intent filter 与 change summary
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/llm/evaluator.py；llm/client.py；worker.py
关键类或函数：evaluate_change；summarise_change；completion
解决的问题：对已发现的网页变化做语义重要性判断和摘要
依赖条件：LiteLLM provider、密钥/本地模型、token 预算；模型不确定性
许可证：主体 Apache-2.0；模型/云 provider 条款另审
推荐复用方式：DESIGN_REFERENCE
复用难度：中
是否建议采用：暂不作为主 AI 层；可在 sidecar 明确配置时评估
验证状态：SOURCE_VERIFIED（实际模型调用 UNVERIFIED）
```

## 7. 文件型证据快照

```text
能力名称：per-watch 原子配置与历史快照
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/store/file_saving_datastore.py；model/Watch.py
关键类或函数：save_json_atomic；save_history_blob；get_history_snapshot
解决的问题：单机监控状态、内容版本和证据可恢复保存
依赖条件：单进程、文件系统、备份；无复杂查询
许可证：Apache-2.0 主体
推荐复用方式：DESIGN_REFERENCE
复用难度：中
是否建议采用：只保留为 changedetection 私有 datastore；不作即时 AI 主库
验证状态：SOURCE_VERIFIED
```

## 8. Pluggy 扩展机制

```text
能力名称：fetcher/processor/lifecycle hooks
来源项目：dgtlmoon/changedetection.io
提交哈希：fce24780e74199bf34c62a0d90188cc2fc12f061
源码位置：changedetectionio/pluggy_interface.py；processors/__init__.py
关键类或函数：ChangeDetectionSpec；load_setuptools_entrypoints；find_processors
解决的问题：在不直接修改 upstream 的情况下增加抓取器、processor 和 UI metadata
依赖条件：可信 Python package、插件版本/许可证、进程内执行权限
许可证：主体 Apache-2.0；每个插件独立审查
推荐复用方式：ADAPTER
复用难度：中
是否建议采用：仅当 REST/RSS 无法满足，建立独立插件包且经过安全审查
验证状态：SOURCE_VERIFIED
```

## 明确拒绝

- `FORK_CORE` 作为即时 AI 主底座：`REJECT`（领域不匹配、内部耦合、许可证材料待澄清）。
- 复制 `worker.py`、`flask_app.py` 或 Watch 文件存储进入正式产品：`REJECT`。
- 把 changedetection datastore 当统一财经数据库：`REJECT`。

