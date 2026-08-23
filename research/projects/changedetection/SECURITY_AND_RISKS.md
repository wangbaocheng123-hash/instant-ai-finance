# changedetection.io 安全与风险

## 已有安全控制（源码验证）

### SSRF 与 URL

- `validate_url.py::is_fetch_url_allowed` 是常规抓取统一 gate：默认拒绝 `file://`、反斜杠 parser differential、非 allowlist 协议及私网/保留/多播/CGNAT 等地址。
- `requests` fetcher 在实际 request/redirect 时再次校验目标和 DNS，降低 DNS rebinding/redirect 绕过。
- LLM `api_base` 有 `is_llm_api_base_safe`；设置页面还防止把已存 API key 发到新 endpoint。

### 路径与备份

- `Watch.history` 和 `get_history_snapshot` 把快照读取限制在 watch 目录。
- processor 配置名和路径受 bare filename/realpath 校验。
- backup restore 检查 zip slip、上传大小和解压总量。

### Web/API

- Web 写操作使用 CSRF；API resource 单独 exempt 后用 `x-api-key`。
- 登录支持本地 password/SALTED_PASS；共享 diff 只开放明确 read-only endpoint。
- 通知 HTML 对 page/diff 变量转义，源码注释明确针对历史 GHSA 注入问题。
- safe Jinja 使用 `ImmutableSandboxedEnvironment` 并限制返回 payload。

## 主要风险

### 1. 默认网络暴露和认证

进程默认监听 `0.0.0.0:5000`，而全局 password 默认 false；API token 默认开启，但 UI 若不设密码可能对局域网可见。Windows sidecar 必须绑定 `127.0.0.1`，设置密码/API token，并由桌面进程控制访问。

证据：`changedetectionio/__init__.py::main`、`model/App.py::base_config`、`api/auth.py::check_token`。状态：`SOURCE_VERIFIED`。

### 2. 高风险开关可关闭关键防线

`ALLOW_FILE_URI`、`ALLOW_IANA_RESTRICTED_ADDRESSES`、`SAFE_PROTOCOL_REGEX` 和 `HISTORY_SNAPSHOT_FILE_ALLOW_OUTSIDE_WATCH_DATADIR` 可放宽本地文件、私网/保留地址、协议或目录限制。服务若允许低信任用户改配置，会形成 SSRF/本地文件/证据路径风险。

状态：`SOURCE_VERIFIED`。

### 3. 监控目标与凭据

watch 支持自定义 headers、body、browser steps、JS、proxy 和通知 URL；这些可能包含 Cookie/token。它们写入 `watch.json`、headers 文件或进程环境，属于敏感数据；datastore 必须限制 ACL、备份和日志。

状态：`SOURCE_VERIFIED`。

### 4. 插件供应链与任意代码

Pluggy 加载 setuptools entry points；容器 `EXTRA_PACKAGES` 直接传给 pip。插件与主进程同权限，可读 datastore/密钥并执行任意代码。只允许锁版本、来源和许可证均审查通过的插件；不要把 package name 暴露给普通用户。

状态：`SOURCE_VERIFIED`。

### 5. 单进程文件存储

文件保存明确不支持多进程共用 datastore。并行启动可能造成状态覆盖、history index 竞争或不一致。即时 AI 应把它当单实例 sidecar，不共享 datastore 给多个实例或直接修改文件。

状态：`SOURCE_VERIFIED`。

### 6. 线程/资源与队列

worker pool、queue executor、ticker、Socket.IO 和 notification workers 都在同一进程；`FETCH_WORKERS` 没有代码内硬上限，截图、浏览器、PDF、LLM 和大页面可放大内存/连接数。虽有 queue 5000、worker restart、snapshot max 和输入上限等控制，运行时仍需压测。

状态：`SOURCE_VERIFIED`；实际容量 `UNVERIFIED`。

### 7. LLM 数据与费用

变化内容、diff、intent 可能发送给外部 provider；API key 可能写入 JSON；模型可误判重要性。源码有 token budget 和 fail-open 路径，但不能替代数据分类、费用告警和人工审计。“即时 AI”若统一 AI 层，建议 sidecar 禁用 LLM。

状态：`SOURCE_VERIFIED`；provider 行为 `UNVERIFIED`。

### 8. 通知内容与目标

Apprise 支持大量 URL schema 和自定义 HTTP；错误配置可把证据/敏感内容发往第三方。HTML escaping 已有强化，但 URL、本地/私网目标、模板和附件仍需 allowlist 与 egress policy。

状态：`SOURCE_VERIFIED`。

### 9. 版本检查/遥测

`check_for_new_version` 每日向 `https://changedetection.io/check-ver.php` POST version、app_guid、watch_count，并关闭 TLS verify。虽然未发送 URL 内容，严格隐私部署应设置 `DISABLE_VERSION_CHECK=true`；关闭验证本身也扩大中间人风险。

证据：`flask_app.py::check_for_new_version`。状态：`SOURCE_VERIFIED`。

### 10. 内容授权与投资用途

技术上能抓取并保存网页，不代表获得复制/再分发权。必须遵守目标站条款、版权、robots/频率和访问授权，不绕过付费墙。变化通知不能直接转化为自动买卖指令；财经判断仍需来源交叉验证。

状态：项目约束 + 架构推论；具体站点合规 `UNVERIFIED`。

## 未验证项

- 未运行 SAST、dependency audit、SBOM、pytest 或容器扫描；
- 未验证当前提交是否包含尚未修复 CVE/GHSA；
- 未验证反向代理、Socket.IO、browser service 和通知 provider 的真实安全配置；
- 无 `.git`，未检查提交签名、历史或供应链 provenance。

## “即时 AI”接入安全基线

1. 只绑定 loopback；
2. 独立低权限进程和 datastore；
3. API token 存系统秘密库，不进仓库；
4. 主系统通过 API 拉证据，不直接写 datastore；
5. 高风险环境开关默认关闭；
6. watch header/cookie 不进入日志/研究仓库；
7. 事件进入主系统后再次做 URL/内容校验与幂等；
8. 插件、browser image、通知目标均采用 allowlist；
9. 数据写入 H 盘须遵循已批准的业务文件库边界；
10. 正式部署前完成运行验证、依赖扫描和威胁建模。

项目/提交：`dgtlmoon/changedetection.io` / `fce24780e74199bf34c62a0d90188cc2fc12f061`。

