# RSSHub 可复用能力

> 统一证据标识：`DIYgod/RSSHub`，提交
> `5151c3233bc7bacfaecc6e4f01aba2b60022d683`，
> `upstream/RSSHub-snapshot`（`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不等于完成克隆）。

以下结论仅用于 R0 选型，不授权把 AGPL 源码复制到正式产品。项目与提交对所有条目均为：
`DIYgod/RSSHub` @ `5151c3233bc7bacfaecc6e4f01aba2b60022d683`。

## 1. RSS 数据源服务

```text
能力名称：多来源到标准 Feed 的转换服务
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/index.ts；lib/app-bootstrap.tsx；lib/registry*.ts；lib/routes/**
关键类或函数：serve；registerRssRoutes；Route.handler
解决的问题：把不提供 RSS 或 RSS 不完整的来源统一为 RSS/Atom/JSON Feed
依赖条件：Node 22.22.2/24.15.0 或容器；逐 Route 网络/凭据/浏览器依赖
许可证：AGPL-3.0
推荐复用方式：SIDE_CAR_SERVICE
复用难度：中
是否建议采用：建议进入受控运行验证；当前静态推荐
```

理由：HTTP 边界保留独立部署和许可证边界，也最大化利用上游 Route 生态。

## 2. 财经与公告 Route 集合

```text
能力名称：财经媒体、交易所公告、研报和黄金行业来源适配
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/routes/cninfo；sse；szse；xueqiu；jin10；cls；eastmoney；cngold
关键类或函数：各 route 导出的 Route 与 handler
解决的问题：快速覆盖公告、快讯、研报、公司动态和黄金产业来源
依赖条件：逐路由验证来源可用性、频率、Cookie、反爬和使用条款
许可证：RSSHub 路由代码 AGPL-3.0；来源内容权利与站点条款另行审查
推荐复用方式：API_INTEGRATION
复用难度：中高（维护主要受上游站点变化影响）
是否建议采用：有选择地采用；先验收官方公告和公开来源
```

## 3. Route 元数据与自动注册模式

```text
能力名称：Namespace/Route 元数据驱动的 Provider 注册
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/types.ts；lib/registry-helpers.ts；scripts/workflow/build-routes.ts
关键类或函数：Route；Namespace；applyModulesToNamespaces；registerRssRoutes
解决的问题：隔离来源模块，统一 path、参数、依赖、维护者、分类和发现规则
依赖条件：若直接复用需 Hono 与 RSSHub 构建体系
许可证：AGPL-3.0
推荐复用方式：REWRITE_FROM_PATTERN
复用难度：中
是否建议采用：建议借鉴接口思想，不复制整套实现
```

对“即时 AI”可重写为自有 `SourceAdapter` 合同，并增加来源权威级别、证据策略、速率限制、
首次发现时间和持久化策略。

## 4. Feed 统一模型

```text
能力名称：Data/DataItem 规范化模型
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/types.ts
关键类或函数：Data；DataItem
解决的问题：统一标题、正文、日期、链接、分类、作者、guid 和媒体字段
依赖条件：作为设计参考无运行依赖
许可证：AGPL-3.0
推荐复用方式：DESIGN_REFERENCE
复用难度：低
是否建议采用：建议参考后扩展，不直接作为正式数据库模型
```

缺口：没有 source provenance、抓取时间、内容哈希、实体、事件、重要度和证据版本。

## 5. 两级缓存与并发抑制

```text
能力名称：路由缓存、内容缓存与同路由并发 claim
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/middleware/cache.ts；lib/utils/cache/**
关键类或函数：middleware；tryGet；globalCache.claim；CacheModule
解决的问题：降低上游请求量、防止缓存击穿、缓存详情和 AI 结果
依赖条件：memory/Redis/HTTP/KV 后端；HTTP/KV claim 仅 best-effort
许可证：AGPL-3.0
推荐复用方式：DESIGN_REFERENCE
复用难度：中
是否建议采用：建议借鉴；正式核心应使用自有可测试实现
```

## 6. 通用参数过滤与全文处理

```text
能力名称：Feed 通用筛选、限量、全文抽取与可选 OpenAI 后处理
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/middleware/parameter.ts
关键类或函数：middleware；makeRegex；getAiCompletion
解决的问题：在来源 Route 之外统一处理 filter/filterout/fulltext/chatgpt 等参数
依赖条件：RE2JS、Cheerio、Mercury Parser、OpenAI-compatible API 和缓存
许可证：AGPL-3.0
推荐复用方式：DESIGN_REFERENCE
复用难度：高
是否建议采用：仅参考；AI、筛选和审计应在即时 AI 自有流水线实现
```

## 7. npm 包内嵌模式

```text
能力名称：进程内 init/request/registerRoute
来源项目：DIYgod/RSSHub
提交哈希：5151c3233bc7bacfaecc6e4f01aba2b60022d683
源码位置：lib/pkg.ts
关键类或函数：init；request；registerRoute
解决的问题：不走外部 HTTP 进程即可调用和扩展 RSSHub
依赖条件：完整 RSSHub 运行依赖和构建产物
许可证：AGPL-3.0；与正式客户端的组合作品边界需法律审查
推荐复用方式：REJECT
复用难度：高
是否建议采用：当前不建议；sidecar 更清晰
```

## 采用优先级

1. `SIDE_CAR_SERVICE`：独立 RSSHub，localhost HTTP Feed。
2. `API_INTEGRATION`：只启用验收过的财经 Route。
3. `REWRITE_FROM_PATTERN/DESIGN_REFERENCE`：来源适配合同、缓存和模型思想。
4. `REJECT`：未经许可证评审的进程内嵌、整仓混合或复制 Route 源码。
