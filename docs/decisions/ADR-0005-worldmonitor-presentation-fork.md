# ADR-0005：以许可证隔离的 World Monitor 分叉承载全球财经文字终端

- 状态：`ACCEPTED`
- 日期：2026-08-24
- 决策者：用户要求按讨论方案直接改造

## 背景

用户希望保留 World Monitor 高密度、模块化、即时滚动的展示方式，但产品内容必须聚焦全球财经，不需要 YouTube、直播摄像头、医疗等大流量或非财经模块，也不接受账户体系、付费云端回退或对 World Monitor 服务的运行依赖。

World Monitor `v2.5.23` 的固定提交为 `e51058e1765ef2f0c83ccb1d08d984bc59d23f10`，许可证为 AGPL-3.0-only。原项目功能面远大于即时 AI 的单机个人场景。

## 备选项

1. 原样运行 World Monitor，再用配置隐藏不需要的模块。
2. 完全脱离上游，从空白页面重新实现类似界面。
3. 保留独立 Git 分叉和许可证历史，只启用一个窄化的自定义财经运行入口，通过 localhost API 使用既有即时 AI 核心。

## 决定

采用方案 3：

- 原始上游只读保存在 `upstream/WorldMonitor`，不直接修改。
- 可修改分叉独立保存在 `forks/InstantAI-WorldMonitor`，分支为 `codex/instant-ai-finance`，首个改造提交为 `9682a944c9c45c5a081feee397db4f8a77be9203`。
- 分叉继续完整保留 AGPL-3.0-only 许可证和上游版权记录；部署产物同时携带 `AGPL-3.0.txt` 与 `NOTICE.txt`。
- `src/main.ts` 只启动 `InstantFinanceApp`；前端只调用 `127.0.0.1:18765/api`，不调用 `api.worldmonitor.app`。
- 删除 YouTube API、直播新闻、直播摄像头、HLS 测试和 YouTube 登录 capability；CSP 禁止媒体、frame 和外部 connect。
- 业务数据库、原文、证据、备份、缓存和日志继续由 Python 本地核心写入 `H:\即时AI文件库`。分叉不建立第二业务主库。

## 理由

该方案兼顾了可追溯开源复用、快速交付和运行边界。保留上游历史便于以后审计或选择性移植成熟模块；窄化入口又能避免把地图、视频、账户和云服务带进个人财经客户端。

## 影响

- 当前桌面壳仍为 Edge 应用模式，ADR-0004 的 localhost 与 H 盘边界不变；变化的是壳内展示层。
- 对外分发或商业化前必须重新审查 AGPL 源码提供、修改声明和网络交互义务；当前仅供本机所有者个人使用。
- Reuters、Bloomberg、FT、WSJ 等专业内容若没有授权，只能使用公开标题发现和原文链接，不得把发现 Feed 描述成授权全文服务，也不得绕过付费墙。
- 若未来撤销本分叉，只需替换静态展示产物；本地 API、SQLite schema 和 H 盘证据不随之迁移。

## 证据

- 上游锁定与分叉提交：`UPSTREAM_LOCK.yaml`
- 源码复用和删除清单：`research/WORLDMONITOR_FINANCE_FORK.md`
- 分叉说明：`forks/InstantAI-WorldMonitor/INSTANT_AI_FORK.md`
- 部署许可：`product/instant_ai/static/AGPL-3.0.txt`、`product/instant_ai/static/NOTICE.txt`

## 关系

本 ADR 补充 ADR-0004 的展示层决定，不替代其 Python 本地核心、SQLite、localhost、Edge 应用壳和 H 盘数据边界。
