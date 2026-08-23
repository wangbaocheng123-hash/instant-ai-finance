# 原始总指令（R0 开源研究阶段）

> 来源：用户在 2026-08-23 提供的第一阶段总指令。
> 本文件是不可擅自改写的需求基线；具体执行状态以 `STATUS.md` 和 `TASKS.md` 为准。

---

# Codex第一阶段总指令：开源金融情报系统研究、运行验证与复用选型

你现在是本项目的首席开源架构师、代码考古工程师和技术尽调负责人。

本项目暂定名称：

**FinanceIntelLab / 金融情报系统开源研究实验室**

本轮不开发最终产品。

本轮唯一目标是：

> 下载、运行、阅读和比较现有成熟开源项目，弄清楚它们的真实代码结构、数据流、运行方式、扩展方式、许可证和可复用能力，再决定我们最终以哪个项目为主底座，哪些项目作为独立服务，哪些项目只借鉴设计。

未经用户明确批准，不得从零重新实现已有成熟功能。

---

## 一、项目最终目标

未来建立一个运行在Windows电脑上的个人财经信息客户端，服务于以下投资研究领域：

1. AI产业链；
2. 紫金矿业；
3. 黄金；
4. 铜和有色金属；
5. 与上述领域有关的宏观政策、财报、产业变化和重大事件。

未来系统需要实现：

* 多来源财经信息采集；
* 官方公告和专业财经媒体监测；
* 信息去重；
* 公司、商品和产业实体识别；
* 事件分类；
* 重要度评分；
* AI筛选和摘要；
* 只推送真正重要的信息；
* 保存原文来源和证据；
* 建立个人长期投资信息数据库；
* Windows电脑本地使用；
* 后期再考虑手机端和云端。

---

## 二、本阶段的最高原则

必须遵守：

1. 开源优先；
2. 复用优先；
3. 研究完成前不锁定最终技术栈；
4. 研究完成前不建立正式产品代码；
5. 不把多套源码直接混合到一个目录；
6. 所有原始项目必须独立保留Git历史和许可证；
7. 每一个复用结论必须有源码证据；
8. 不得仅根据README或官网宣传下结论；
9. 必须查看实际入口文件、核心模块、数据结构和运行流程；
10. 不得声称“已经了解项目”，除非已经阅读核心代码并完成结构报告；
11. 不得绕过付费墙或非授权抓取媒体内容；
12. 不得自动交易；
13. 不得给出自动买卖指令；
14. 不得在用户未批准的情况下安装Docker、WSL、全局Node环境或其他大型系统组件；
15. 不得修改Windows系统级配置；
16. 不得把任何密钥写进仓库。

---

## 三、研究对象

研究以下六个正式仓库：

1. `sansan0/TrendRadar`
2. `DIYgod/RSSHub`
3. `dgtlmoon/changedetection.io`
4. `OpenBB-finance/OpenBB`
5. `RSSNext/Folo`
6. `n8n-io/n8n`

不得使用来历不明的Fork替代正式仓库。

必须记录：

* 仓库全名；
* 默认分支；
* 当前提交哈希；
* 下载日期；
* 许可证；
* 主要语言；
* 当前版本或标签；
* 是否仍然活跃；
* 是否有Windows运行说明。

---

## 四、建立永久研究工作区

在当前项目根目录创建：

```text
AGENTS.md
PROJECT_CHARTER.md
STATUS.md
ROADMAP.md
TASKS.md
CHANGELOG.md
UPSTREAM_LOCK.yaml
.gitignore

docs/
  RESEARCH_METHOD.md
  EVALUATION_CRITERIA.md
  ARCHITECTURE_TARGET.md
  LICENSE_POLICY.md
  decisions/

upstream/
  TrendRadar/
  RSSHub/
  changedetection/
  OpenBB/
  Folo/
  n8n/

research/
  projects/
    TrendRadar/
    RSSHub/
    changedetection/
    OpenBB/
    Folo/
    n8n/
  patterns/
    information-ingestion/
    deduplication/
    filtering-and-scoring/
    ai-analysis/
    data-storage/
    notifications/
    financial-data/
    workflow-orchestration/
    desktop-ui/
  FEATURE_MATRIX.md
  LICENSE_MATRIX.md
  ARCHITECTURE_COMPARISON.md
  INTEGRATION_OPTIONS.md
  REUSE_DECISION.md
  MVP_BLUEPRINT.md

experiments/
  TrendRadar-lab/
  RSSHub-lab/
  changedetection-lab/
  OpenBB-lab/
  Folo-lab/
  n8n-lab/

scripts/
  inspect-environment.ps1
  clone-upstreams.ps1
  report-upstream-status.ps1
  clean-build-artifacts.ps1

product/
  README.md
```

`product/README.md`中必须明确：

> 正式产品尚未开始，必须等待开源尽调和底座选型获得用户批准。

---

## 五、原始项目管理规则

`upstream/`中的仓库属于研究原件。

必须遵守：

1. 原始仓库保持独立Git仓库；
2. 不提交到研究主仓库；
3. 根目录 `.gitignore` 忽略 `upstream/`中的源码；
4. 通过 `UPSTREAM_LOCK.yaml`保存每个项目的仓库名和提交哈希；
5. 不在原始仓库内直接修改代码；
6. 不更改原始远程地址；
7. 不删除原始许可证；
8. 不把多个项目源码复制到同一个目录；
9. 所有测试性改动放进对应的 `experiments/`；
10. 未来确需修改某个项目时，必须建立独立Fork或补丁目录。

暂不使用Git Submodule，除非后续ADR明确批准。

---

## 六、节省网络流量的下载策略

先执行环境检查，确认：

* Git版本；
* Python版本；
* Node.js版本；
* pnpm、npm或yarn是否存在；
* Docker是否存在；
* WSL是否存在；
* 可用磁盘空间；
* 当前PowerShell版本。

不要自动安装缺失工具。

下载分成两步。

### 第一步：轻量克隆

所有项目优先使用：

```powershell
git clone --depth 1 --filter=blob:none
```

不得：

* 下载完整Git历史；
* 执行 `git clone --mirror`；
* 主动下载Git LFS大文件；
* 下载无关发行包；
* 在所有项目中同时安装依赖；
* 把 `node_modules`、虚拟环境或编译产物提交到仓库。

### 第二步：按需读取

对于较大的仓库，优先查看：

* README；
* LICENSE；
* pyproject.toml；
* requirements文件；
* package.json；
* workspace配置；
* Docker配置；
* 程序入口；
* 核心源码目录；
* API目录；
* 数据存储目录；
* 抓取器目录；
* AI目录；
* 推送目录；
* 测试目录。

只有分析需要时才读取更多文件。

在 `UPSTREAM_LOCK.yaml`记录：

```yaml
name:
repository:
default_branch:
commit:
license:
clone_mode:
download_date:
analysis_status:
runtime_status:
```

---

## 七、使用并行代码考古

若当前Codex环境支持子智能体，为六个项目分别建立代码考古子任务。

每个子智能体只负责一个项目。

主智能体负责：

* 统一分析标准；
* 检查报告质量；
* 交叉验证结论；
* 生成总对比矩阵；
* 不允许子智能体直接修改正式产品。

不得让六个子智能体使用不同的评价口径。

---

## 八、每个项目必须完成的代码分析

每个项目都必须在：

```text
research/projects/<项目名称>/
```

生成以下文件：

```text
SUMMARY.md
ARCHITECTURE.md
ENTRYPOINTS.md
DATA_FLOW.md
MODULE_MAP.md
DATABASE_AND_STORAGE.md
CONFIGURATION.md
EXTENSION_POINTS.md
REUSABLE_COMPONENTS.md
WINDOWS_RUNBOOK.md
LICENSE_NOTES.md
SECURITY_AND_RISKS.md
TEST_RESULTS.md
FINAL_ASSESSMENT.md
```

### 1. SUMMARY.md

回答：

* 项目到底解决什么问题；
* 目标用户是谁；
* 核心功能是什么；
* 是否适合个人财经情报系统；
* 与我们的需求重合多少；
* 项目最强的五项能力；
* 项目最大的五项问题。

### 2. ARCHITECTURE.md

必须根据源码而不是宣传材料描述：

* 程序入口；
* 前端；
* 后端；
* 调度器；
* 数据源；
* 数据处理；
* 存储；
* AI；
* 通知；
* API；
* MCP；
* 外部依赖。

画出文字架构图和Mermaid架构图。

### 3. ENTRYPOINTS.md

列出：

* 主启动文件；
* 命令行入口；
* Web入口；
* API入口；
* Docker入口；
* 定时任务入口；
* 关键配置文件。

每一项都必须注明源码路径。

### 4. DATA_FLOW.md

必须画出真实数据流程，例如：

```text
信息源
→ 抓取
→ 标准化
→ 去重
→ 分类
→ AI分析
→ 存储
→ 报告
→ 推送
```

并注明各阶段对应的文件、类、函数或模块。

### 5. MODULE_MAP.md

列出核心目录和模块：

* 模块名称；
* 作用；
* 入口；
* 依赖；
* 是否独立；
* 是否可复用；
* 复用难度；
* 许可证影响。

### 6. DATABASE_AND_STORAGE.md

回答：

* 使用什么数据库或文件格式；
* 数据模型如何定义；
* 新闻如何保存；
* 是否保留原始链接；
* 是否支持去重；
* 是否支持历史查询；
* 是否容易迁移；
* 是否适合本地Windows使用。

### 7. EXTENSION_POINTS.md

查找：

* 插件机制；
* Provider机制；
* Adapter机制；
* Webhook；
* API；
* MCP；
* RSS；
* CLI；
* 数据库接口；
* 自定义节点；
* 自定义数据源。

### 8. REUSABLE_COMPONENTS.md

每个可复用能力必须记录：

```text
能力名称：
来源项目：
提交哈希：
源码位置：
关键类或函数：
解决的问题：
依赖条件：
许可证：
推荐复用方式：
复用难度：
是否建议采用：
```

推荐复用方式只能选择：

```text
FORK_CORE
SIDE_CAR_SERVICE
LIBRARY_DEPENDENCY
API_INTEGRATION
ADAPTER
DESIGN_REFERENCE
REWRITE_FROM_PATTERN
REJECT
```

### 9. WINDOWS_RUNBOOK.md

记录：

* Windows能否直接运行；
* 是否需要WSL；
* 是否需要Docker；
* Python或Node版本；
* 启动命令；
* 依赖安装方式；
* 端口；
* 配置；
* 常见错误；
* 停止和清理方法。

### 10. TEST_RESULTS.md

记录：

* 是否成功启动；
* 启动截图或日志；
* 是否成功抓到数据；
* 是否成功保存数据；
* 是否成功展示；
* 是否成功调用API；
* 遇到的问题；
* 使用了多少磁盘空间；
* 是否产生大量无关依赖。

---

## 九、源码证据标准

任何重要结论必须附带：

* 项目名称；
* 提交哈希；
* 文件路径；
* 类名、函数名或配置项；
* 结论说明。

禁止只写：

```text
这个项目支持AI分析。
```

必须写成类似：

```text
项目：
提交：
源码：
类或函数：
调用关系：
结论：
```

不能找到源码证据时，必须标注：

```text
仅在文档中发现，尚未通过源码验证。
```

---

## 十、优秀能力提取规则

用户提出要把各项目优秀点集中到一个文件夹。

正确实现方式是在：

```text
research/patterns/
```

按能力建立模式文档。

例如：

```text
research/patterns/deduplication/
  PATTERN.md
  SOURCE_INDEX.md
  COMPARISON.md
  RECOMMENDATION.md
```

`PATTERN.md`必须包括：

* 该能力解决什么问题；
* 哪些项目实现了；
* 各自如何实现；
* 最好的实现是哪一个；
* 为什么；
* 依赖和限制；
* 推荐采用Fork、服务、API还是重写模式；
* 原始源码位置；
* 许可证影响；
* 对我们系统的建议接口。

默认不得复制整段源码。

如确实需要保留少量代码示例：

* 必须标明来源；
* 必须标明提交哈希；
* 必须保留许可证和版权说明；
* 必须放入独立的参考目录；
* 不得直接进入正式产品；
* 不得删除作者信息。

---

## 十一、项目运行验证顺序

不要一开始为六个项目全部安装依赖。

先完成静态代码分析，然后按照以下顺序评估运行：

### 第一优先级

1. TrendRadar；
2. changedetection.io。

原因是它们最接近信息监控主链路。

### 第二优先级

3. RSSHub；
4. OpenBB。

原因是它们分别负责信息源扩展和结构化金融数据。

### 第三优先级

5. Folo；
6. n8n。

原因是它们体量较大，且更适合作为界面参考和独立工作流系统。

如果某项目需要安装大型依赖、Docker、WSL或修改系统设置：

1. 停止安装；
2. 在报告中说明；
3. 给出预计下载量、磁盘占用和风险；
4. 等待用户批准；
5. 继续完成静态分析，不得让整个任务停滞。

---

## 十二、统一评分标准

在 `EVALUATION_CRITERIA.md`建立100分制：

| 评价项目          | 分数 |
| ------------- | -: |
| 与财经情报需求匹配度    | 20 |
| 已有功能完整度       | 15 |
| 代码可维护性        | 10 |
| 扩展和适配能力       | 10 |
| Windows本地运行能力 | 10 |
| 数据来源能力        | 10 |
| AI与过滤能力       | 10 |
| 上游活跃度         |  5 |
| 许可证适配性        |  5 |
| 改造成本          |  5 |

每个项目必须给出：

* 总分；
* 每项分数；
* 分数理由；
* 证据；
* 推荐角色。

推荐角色只能选择：

```text
PRIMARY_BASE
CORE_FORK_CANDIDATE
SIDE_CAR_SERVICE
DATA_PROVIDER
WORKFLOW_ENGINE
UI_REFERENCE
PATTERN_REFERENCE
NOT_RECOMMENDED
```

---

## 十三、必须重点验证的初始假设

以下只是待验证假设，不得直接当成结论：

1. TrendRadar可能适合作为主底座或核心Fork；
2. RSSHub可能适合作为独立信息源服务；
3. changedetection.io可能适合作为独立网页变化监控服务；
4. OpenBB可能适合作为行情和金融数据提供器；
5. n8n可能适合作为可选工作流编排器；
6. Folo可能主要适合作为阅读界面和交互参考；
7. Windows客户端可能采用本地Web界面加桌面壳；
8. PySide6、Tauri和Electron暂时都不锁定；
9. 最终数据库暂时不锁定；
10. 是否使用Docker暂时不锁定。

必须通过源码、运行验证和许可证分析确认或否定这些假设。

---

## 十四、必须生成的总报告

完成各项目研究后，生成：

### `FEATURE_MATRIX.md`

按功能比较：

* 抓取；
* RSS；
* 网页变化；
* 调度；
* 去重；
* 分类；
* 搜索；
* 数据库；
* AI摘要；
* AI筛选；
* 推送；
* Webhook；
* API；
* MCP；
* 行情数据；
* 宏观数据；
* Windows客户端；
* 插件能力；
* 可扩展性。

### `LICENSE_MATRIX.md`

记录：

* 许可证；
* 是否允许修改；
* 是否允许个人使用；
* 是否允许分发；
* 是否允许商用；
* 是否包含网络服务条款；
* 是否适合Fork；
* 是否适合嵌入；
* 是否建议保持独立服务；
* 尚需确认的问题。

不得把该文件当成正式法律意见。

### `ARCHITECTURE_COMPARISON.md`

比较：

* 技术栈；
* 模块边界；
* 数据流；
* 存储方式；
* 扩展方式；
* 部署方式；
* 维护难度；
* Windows适配。

### `INTEGRATION_OPTIONS.md`

至少给出三种组合方案：

#### 方案A：TrendRadar主底座

TrendRadar作为核心，其他项目通过服务或API接入。

#### 方案B：自建轻量核心

不Fork任何完整系统，只复用独立服务和设计模式。

#### 方案C：工作流平台主导

以n8n为工作流中心，其他项目作为数据节点。

每种方案给出：

* 优点；
* 缺点；
* 开发工作量；
* 维护成本；
* 许可证风险；
* Windows运行难度；
* 后期扩展能力。

### `REUSE_DECISION.md`

必须明确提出：

* 推荐的主底座；
* 推荐的独立服务；
* 推荐的数据提供器；
* 推荐的UI参考；
* 不建议采用的部分；
* 哪些能力直接Fork；
* 哪些能力通过API；
* 哪些能力需要自己写；
* 选择理由；
* 尚未确认的问题。

这只是建议，不得直接执行产品开发。

### `MVP_BLUEPRINT.md`

根据研究结果设计第一版最小产品，但不写正式代码。

内容包括：

* 推荐技术栈；
* 推荐客户端形式；
* 推荐主底座；
* 推荐数据流；
* 推荐数据库；
* 第一批数据源；
* 第一批界面；
* 第一批筛选规则；
* 第一阶段不做什么；
* 预计实施顺序；
* 每一步验收条件。

---

## 十五、永久项目记忆

创建简短的 `AGENTS.md`，要求Codex以后每次进入项目先读取：

1. `PROJECT_CHARTER.md`
2. `STATUS.md`
3. `ROADMAP.md`
4. `UPSTREAM_LOCK.yaml`
5. `research/REUSE_DECISION.md`
6. `research/MVP_BLUEPRINT.md`
7. 与当前任务相关的项目研究报告

写入以下永久规则：

* 开源优先；
* 复用优先；
* 不重复制造成熟功能；
* 原始项目保持独立；
* 不混合许可证不明的源码；
* 重大架构决定必须建立ADR；
* 未经用户批准不进入正式产品开发；
* 每次任务结束更新STATUS和CHANGELOG；
* 不确定时重新读取研究报告，不凭聊天记忆猜测。

---

## 十六、Git要求

研究主目录初始化为Git仓库。

只提交：

* 研究文档；
* 分析报告；
* 脚本；
* 架构图；
* 配置模板；
* UPSTREAM_LOCK.yaml；
* AGENTS.md；
* STATUS.md；
* ROADMAP.md。

不提交：

* `upstream/`源码；
* `node_modules/`；
* `.venv/`；
* Docker镜像；
* 数据库；
* 日志；
* 下载缓存；
* 构建产物；
* 密钥；
* Cookies；
* 登录信息。

完成第一阶段后建立本地提交：

```text
R0-open-source-reconnaissance
```

不得推送远程仓库。

---

## 十七、本轮停止条件

本轮完成以下内容后立即停止：

1. 研究工作区建立；
2. 六个项目完成轻量克隆；
3. UPSTREAM_LOCK.yaml完成；
4. 六个项目完成静态源码分析；
5. 至少TrendRadar完成运行尝试；
6. 能运行的项目留下运行报告；
7. 不能运行的项目说明阻塞原因；
8. FEATURE_MATRIX完成；
9. LICENSE_MATRIX完成；
10. ARCHITECTURE_COMPARISON完成；
11. INTEGRATION_OPTIONS完成；
12. REUSE_DECISION完成；
13. MVP_BLUEPRINT完成；
14. STATUS更新；
15. 建立本地Git检查点。

不得：

* 开始开发正式客户端；
* 重写新闻抓取器；
* 新建正式数据库；
* 修改TrendRadar源码；
* 实现AI评分；
* 实现手机推送；
* 决定最终UI框架；
* 直接把几个项目代码混在一起。

---

## 十八、最终汇报格式

最终按照以下顺序汇报：

1. 当前阶段；
2. 已研究的项目；
3. 每个项目是否成功下载；
4. 每个项目是否成功运行；
5. 每个项目最值得复用的能力；
6. 每个项目最大的风险；
7. 许可证结论；
8. 推荐的主底座；
9. 推荐的独立服务；
10. 推荐的整体组合架构；
11. 推荐的Windows客户端形式；
12. 本轮生成的文件；
13. 下载量和磁盘占用；
14. 当前阻塞事项；
15. 需要用户批准的决策；
16. 下一项唯一建议任务。

现在先进入Plan模式，制定执行计划；确认计划符合本指令后，再开始R0开源项目研究阶段。

