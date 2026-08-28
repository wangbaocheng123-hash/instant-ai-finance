# 任务账本

状态只使用 `NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`DONE`。任务完成必须有证据，不以“创建了空文件”代替研究成果。

| ID | 状态 | 任务 | 验收证据 |
|---|---|---|---|
| G0-001 | DONE | 保存用户 R0 原始总指令 | `docs/FOUNDATION_DIRECTIVE_R0.md` |
| G0-002 | DONE | 建立永久指令和三层记忆协议 | `AGENTS.md`、`docs/MEMORY_PROTOCOL.md` |
| G0-003 | DONE | 固化名称、Windows 桌面形态和 H 盘文件库 | `PROJECT_CHARTER.md`、`config/storage.paths.example.toml` |
| G0-004 | DONE | 建立状态、路线图、变更和上游锁定表 | `STATUS.md`、`ROADMAP.md`、`CHANGELOG.md`、`UPSTREAM_LOCK.yaml` |
| G0-005 | DONE | 建立记忆与泄漏检查脚本 | `scripts/check-project-memory.ps1` |
| G0-006 | DONE | 固定单机单用户、无账户体系并清理多用户规划 | ADR-0003、章程、永久指令与 MVP 蓝图 |
| R0-ENV-01 | DONE | 只读环境盘点，不安装工具 | `docs/ENVIRONMENT_REPORT.md` 与 `STATUS.md` |
| R0-CLONE-01 | DONE | 轻量克隆六个官方仓库并核验锁定提交 | 六仓均为官方 SSH 远程、浅仓且各含一个提交；详见 `docs/CLONE_LOG.md` |
| R0-STATIC-01 | DONE | 按统一模板完成六仓静态代码考古 | 六仓各 14 份规定报告均已通过最小完整性检查 |
| R0-RUN-01 | DONE | 按已授权范围进行受控运行验证 | TrendRadar 已完成隔离启动尝试；六仓 `TEST_RESULTS.md` 均记录结果或依赖审批阻塞 |
| R0-SYNTH-01 | DONE | 完成功能/许可证/架构对比与复用选型 | 六份总报告、九类模式库、ADR-0002 提案 |
| R0-CHECKPOINT-01 | DONE | R0 完成后创建本地 Git 检查点 | 本地提交 `R0-open-source-reconnaissance`，未推送远程 |
| D0-APPROVAL-01 | DONE | 用户审阅并批准分阶段实施方案 | 用户于 2026-08-23 要求继续开发；ADR-0004 |
| P0-BOOTSTRAP-01 | DONE | 建立正式产品工程骨架 | `product/instant_ai/`、本地服务、SQLite 迁移和测试基线 |
| P1-STORAGE-01 | DONE | 建立 H 盘正式库与证据布局 | SQLite/WAL、内容寻址 raw、evidence 运行清单、备份校验 |
| P1-INGEST-01 | DONE | 接入首批官方来源并验证幂等 | 第 5 次正式采集：6 来源、112 条、新增 0、错误 0 |
| P1-RULES-01 | DONE | 实现主题、实体、事件与可解释评分 | `product/instant_ai/rules.py` 与单元测试 |
| P1-API-UI-01 | DONE | 完成本地 API 与阅读界面 | 今日重点、时间线、搜索、详情证据、来源状态、导出 |
| P1-DESKTOP-01 | DONE | 建立桌面启动入口 | `C:\Users\36590\Desktop\即时 AI.lnk`，Edge 应用壳 |
| P1-VERIFY-01 | DONE | 验证核心、接口、数据位置和备份 | `product/tests/`、`scripts/verify-instant-ai.ps1`、SQLite 完整性检查 |
| P2-SOURCE-02 | DONE | 扩展全球财经文字来源与五分钟采集 | 截至第 37 次正式采集：20 来源健康、947 条、来源错误 0；覆盖全球/华尔街/中国/亚洲/黄金矿业/AI/地缘/融资/财经知识 |
| P2-UI-02 | DONE | 以许可证隔离的 World Monitor 分叉建立纯文字财经终端 | 分叉提交 `9682a944c9c45c5a081feee397db4f8a77be9203`；生产构建及本地浏览器频道、搜索、详情、无媒体元素验证通过 |
| P2-I18N-01 | DONE | 在桌面客户端内置英文标题汉化与本机缓存 | 分叉提交 `867a4acbfbbce3b587a4ed5eb61fc062289e7fbb`；SQLite schema 3、MyMemory 安全额度、中英双标题、开关和重复请求缓存经 API/浏览器验证 |
| P2-I18N-02 | DONE | 增加新闻详情按需中文阅读与短期译文缓存 | ADR-0013；0.9.0；公开正文有界提取、Feed 摘要回退、共享安全额度、SQLite schema 6 级联缓存；18 项测试、生产构建和 390×844 手机界面验证通过 |
| P2-UI-03 | DONE | 改为白底黑字并实现左图右文的新闻缩略图 | 初始分叉提交 `a4b5b11b7bde888c51c958e807a54d5ad7a3f20e`；可读性增强提交 `09bac45749ed38aa201f089a81c3a9a2f24232c6` 将新闻标题放大至 15px、来源/发布时间放大至 11px；schema 4、H 盘按需图片缓存、生产构建和本机资源验证通过 |
| P2-UI-04 | DONE | 在“即时消息”旁新增全球热点栏 | 分叉提交 `f7884a12adae6385ea1930c9a20bebb23ba682a5`；从近期已采集全球信息按多来源数、重要度和新鲜度排序，双栏滚动、响应式叠放、生产构建及本机浏览器无错误验证通过 |
| P2-UI-05 | DONE | 压缩顶部状态项并把频道导航改为整页直切 | 分叉提交 `daa3b2eb5cd6e301284c6b4bef4c5fd9877a893e`；0.7.1 生产构建；顶部工具条实测 32×209px；桌面华尔街/黄金与手机华尔街直切只保留一个可见频道，页首位置为 0；14 项测试及运行验收通过 |
| P2-UI-06 | DONE | 把顶部“即时”和“全球热点”合并为“即时热点” | 分叉提交 `658dce646c59aedd02efd265e655085e36155293`；0.7.2 生产构建；最新与热点候选交错去重，桌面仅 1 条、首轮 16 条唯一且含多来源/75/84 分事件，手机仅 1 条且高 36px；14 项测试通过 |
| P2-RUNTIME-01 | DONE | 改为启动即采和持续自动采集，并使用新闻原图与合适尺寸客户端窗口 | ADR-0008；分叉提交 `80ed6a0d8ae68efa57ff676ea48fec02641c5b7f`；第 43 次采集 970 条、20/20 来源健康、最新 10 条中 9 条返回原图，桌面窗口 1240×820 且非最大化 |
| P2-IMAGE-01 | DONE | 修复新采集新闻封面被旧索引和失败缓存长期锁成占位图 | Google News 预览索引/失败重试对齐 5 分钟，新条目使索引失效，占位响应缓存 60 秒并升级 URL 版本；12 项测试通过；客户端首屏 24 个封面中 20 个真实图、4 个明确无图、加载失败 0 |
| P2-RETENTION-01 | DONE | 把产品改为无长期档案的短周期热点雷达 | ADR-0010；0.8.0；普通/重要/最高重要度消息分别保留 72 小时/5 天/7 天；证据每条最多 5 份、备份最多 3 份；正式库清理后完整性正常；16 项测试和运行验收通过 |
| P2-QUALITY-01 | NOT_STARTED | 建立来源质量分层、白名单和同事件聚类 | 低质来源可屏蔽；重复事件可折叠；官方/主流发现/一般发现有明确标识 |
| P2-AI-01 | IN_PROGRESS | 建立可选 AI 后处理接口、证据引用和离线降级 | 未配置密钥时不得阻断入库或冒充 AI 结果 |
| P2-NOTIFY-01 | IN_PROGRESS | 建立低噪声通知 outbox 和 Windows 通知 | 站内 outbox/确认已完成；系统级通知待实现；仅规则阈值触发、无交易动作 |
| P3-RESTORE-01 | DONE | 完成备份恢复演练 | schema 2 带校验备份在 H 盘隔离恢复，`integrity_check=ok`，112 条/6 来源一致，报告留证 |
| P3-MOBILE-01 | DONE | 把即时 AI 展示层调整为个人手机版并提供可安装 Web App 外壳 | 分叉提交 `f3fad86eb4ec75983c6b24830f14120a4cb84606`、预览标题提交 `f182777560cfc5bedc856e9971af0e174ce09a52`；0.7.0 生产构建；390×844 实测单列新闻、原图、全屏详情、五个底部快捷频道；电脑端手机预览启动器与桌面快捷方式；14 项单元测试 |
| P3-GIT-01 | DONE | 建立公开 Git 统一版本源并供本机与云端共同更新 | <https://github.com/wangbaocheng123-hash/instant-ai-finance>；`main` 已推送，`v0.7.1` 至 `v0.9.0` 标签已同步；0.9.0 产品发布提交 `acafde9` |
| P3-CLOUD-01 | DONE | 部署到用户既有阿里云实例并提供手机即时打开入口 | ADR-0011；正式域名与应急地址均返回 0.9.0；独立目录/数据/service/Caddy 导入保持，原 Caddyfile 与 Time Compass 未动；无账户密码，API 不缓存，中文阅读真实样本与短期缓存通过验收 |
| P3-DOMAIN-01 | DONE | 为即时 AI 绑定用户选定的空闲自有域名 | 用户一次性明确授权后，仅在 AliDNS 当前页面把 `grandpaamu.com` 的 `@` 与 `www` A 记录改为 `47.236.175.118`；根域名 HTTPS 返回 0.8.2，`www` 301 跳转根域名，旧 sslip.io 地址和原 Time Compass 均保持可用；ADR-0012 |
| P3-UPGRADE-01 | NOT_STARTED | 建立升级、迁移回滚和独立 EXE 评估 | 升级不删除 H 盘业务库，失败可回退 |

## 当前依赖说明

- 所有 `R0-*` 任务依赖用户授权开始 R0。
- `R0-STATIC-01` 依赖对应仓库完成锁定，但遇到运行依赖阻塞时仍继续静态分析。
- `R0-CLONE-01` 已通过用户手工登记的专用 SSH 公钥完成；该密钥只用于上游源码获取，不属于产品账户层。
- 更深入运行验证依赖用户批准各项目局部依赖安装；当前最低停止条件中的 TrendRadar 运行尝试已满足。
- `D0-APPROVAL-01` 已完成；P1 核心闭环已可用，P2/P3 按任务账本继续推进。
- 真实模型调用依赖用户提供服务选择和安全凭据；在此之前先完成不含密钥的接口与降级路径。
