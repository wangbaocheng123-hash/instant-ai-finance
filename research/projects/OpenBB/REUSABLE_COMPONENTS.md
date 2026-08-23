# OpenBB 可复用组件

统一来源项目：`OpenBB-finance/OpenBB`  
统一提交：`3e071fcc2cd9f891cac6040ae60296dba76dab46`  
原件状态：`OFFICIAL_ARCHIVE_SNAPSHOT`，无 `.git`，不能视为完成 Git 克隆。  
根许可证：AGPL-3.0；所有采用建议都必须经过许可证和 provider 数据条款复核。

## 1. 统一金融数据服务

能力名称：多 provider 的统一金融数据 API  
源码位置：`openbb_platform/core/openbb_core/api/rest_api.py`；`extensions/platform_api/openbb_platform_api/main.py`  
关键类或函数：`FastAPI app`、`AppLoader.add_routers`、`launch_api`  
解决的问题：让“即时 AI”用统一 REST 访问行情、财报、宏观、新闻、监管数据。  
依赖条件：Python `>=3.10,<4`，选定 OpenBB extension/provider，必要 API keys。  
许可证：AGPL-3.0；网络交互和分发边界需法律审查。  
推荐复用方式：`SIDE_CAR_SERVICE`  
复用难度：低到中  
是否建议采用：**建议进入运行验证**，只绑定 localhost，先安装最小 provider 集。  
状态：`SOURCE_VERIFIED`，运行未验证。

## 2. Provider + Fetcher 标准化模式

能力名称：Provider registry 与 TET Fetcher  
源码位置：`core/openbb_core/provider/abstract/provider.py`、`abstract/fetcher.py`、`registry.py`、`query_executor.py`  
关键类或函数：`Provider`、`Fetcher.fetch_data`、`RegistryLoader`、`QueryExecutor.execute`  
解决的问题：统一不同数据源的参数、凭据、抽取和返回模型。  
依赖条件：OpenBB core/Pydantic；直接采用内部类会形成强耦合。  
许可证：AGPL-3.0。  
推荐复用方式：`DESIGN_REFERENCE`  
复用难度：中  
是否建议采用：**建议借鉴接口设计，不复制源码**；产品自有采集层使用许可证清晰的适配器协议。  
状态：`SOURCE_VERIFIED`。

## 3. Python 包接口

能力名称：`from openbb import obb` 直接查询  
源码位置：`openbb_platform/core/openbb/__init__.py`；`app/static/app_factory.py`  
关键类或函数：`obb`、`create_app`、`CommandRunner`  
解决的问题：在 Python 服务内低成本调用结构化金融数据。  
依赖条件：安装对应 OpenBB 包和扩展；import 可能触发自动 build。  
许可证：AGPL-3.0。  
推荐复用方式：`LIBRARY_DEPENDENCY`  
复用难度：低  
是否建议采用：**条件建议**；原型和个人本地服务可评估，正式分发前必须完成法律审查。  
状态：`SOURCE_VERIFIED`，运行未验证。

## 4. 标准金融数据模型

能力名称：跨 provider 的 Query/Data schema  
源码位置：`core/openbb_core/provider/standard_models/`  
关键类或函数：`CompanyNewsData`、`EquityHistoricalData`、`CompanyFilingsData` 等 180 个模型文件  
解决的问题：给同一金融概念定义稳定字段。  
依赖条件：Pydantic/OpenBB base classes。  
许可证：AGPL-3.0。  
推荐复用方式：`DESIGN_REFERENCE`  
复用难度：低到中  
是否建议采用：**建议作为我们 schema 设计的对照表，不直接复制模型源码**。  
状态：`SOURCE_VERIFIED`。

## 5. SEC 与官方宏观数据连接器

能力名称：监管披露和官方宏观/能源数据  
源码位置：`providers/sec`、`providers/federal_reserve`、`providers/eia`、`providers/bls`、`providers/cftc`、`providers/government_us`  
关键类或函数：各包 `Provider.fetcher_dict`；如 `SecCompanyFilingsFetcher`、`FederalReserveFomcDocumentsFetcher`、`EiaPetroleumStatusReportFetcher`  
解决的问题：为公司公告、宏观政策、商品和能源研究提供权威数据。  
依赖条件：部分源需 API key/headers/速率控制；需遵守来源条款。  
许可证：OpenBB 代码 AGPL；原始数据另受来源条款约束。  
推荐复用方式：`API_INTEGRATION`  
复用难度：低到中  
是否建议采用：**建议优先验证 SEC/FRED/FED/CFTC/EIA**，并保留原始 URL/抓取时间。  
状态：`SOURCE_VERIFIED`，外部数据未调用。

## 6. 新闻标准化入口

能力名称：公司新闻和全球新闻统一 schema  
源码位置：`extensions/news/openbb_news/news_router.py`；`standard_models/company_news.py`、`world_news.py`；provider news models  
关键类或函数：`company`、`world`、`FMPCompanyNewsFetcher`、`YFinanceCompanyNewsFetcher`  
解决的问题：把多源新闻转成带日期、标题、正文/摘录和 URL 的记录。  
依赖条件：provider 质量/授权差异大；无持久化、去重、证据快照。  
许可证：AGPL + 新闻来源条款。  
推荐复用方式：`ADAPTER`  
复用难度：中  
是否建议采用：**只作为补充数据源**；不得代替主采集和证据层。  
状态：`SOURCE_VERIFIED`。

## 7. MCP 金融工具服务

能力名称：OpenAPI 到 MCP tools/resources/prompts  
源码位置：`extensions/mcp_server/openbb_mcp_server/app/app.py`、`models/settings.py`  
关键类或函数：`FastMCP` 组装逻辑、`MCPSettings`、route `mcp_config`  
解决的问题：让外部智能体查询金融数据和组合研究步骤。  
依赖条件：FastMCP、OpenBB API；必须做工具白名单和认证。  
许可证：AGPL-3.0。  
推荐复用方式：`SIDE_CAR_SERVICE`  
复用难度：中  
是否建议采用：**MVP 后评估**；不作为首批采集链依赖。  
状态：`SOURCE_VERIFIED`，运行未验证。

## 8. 桌面环境管理器

能力名称：Tauri/React 环境、后端和 Jupyter 管理  
源码位置：`desktop/src`、`desktop/src-tauri/src`  
关键类或函数：`main.rs`、`startup.rs`、`backends.rs`、`environments.rs`  
解决的问题：以 GUI 安装 Miniforge、管理后端进程和配置。  
依赖条件：Rust 1.90、Node/npm、OpenSSL、Tauri，首次运行下载 Miniforge 和 Python/Node/Jupyter 依赖。  
许可证：根/manifest 指向 AGPL；`desktop/LICENSE` MIT 冲突未解。  
推荐复用方式：`DESIGN_REFERENCE`  
复用难度：高  
是否建议采用：**不建议 Fork**；它不是情报阅读 UI，且依赖与许可证成本高。  
状态：`SOURCE_VERIFIED`，运行未验证。

## 明确拒绝

能力名称：把完整 OpenBB 仓库作为“即时 AI”主底座  
源码位置：全仓  
关键类或函数：不适用  
解决的问题：表面上可快速获得金融数据，但不能提供情报闭环。  
依赖条件：完整依赖、动态扩展、桌面环境和多 provider。  
许可证：AGPL 和桌面许可证冲突。  
推荐复用方式：`REJECT`  
复用难度：高  
是否建议采用：**不建议**。应保持为数据 provider/sidecar，而非产品核心。  
状态：`SOURCE_VERIFIED` 的架构判断，待总选型交叉验证。
