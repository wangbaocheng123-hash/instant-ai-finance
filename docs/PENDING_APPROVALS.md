# R0 外部动作与授权记录

状态：`R0_AUTHORIZATIONS_RESOLVED / PRODUCT_IMPLEMENTATION_APPROVED`。

## A. GitHub SSH 公钥登记（已完成）

当前 `github.com:443` 的 HTTPS Git 通路不可达，而 `ssh.github.com:443` 可用。专用 ED25519 密钥已创建，用户已手工登记公钥；六个官方仓库已执行 `--depth 1 --filter=blob:none` 浅克隆并核验固定提交。

私钥未进入仓库或对话。公钥指纹：`SHA256:YBMC0KNFrzWoxjNpWZoMaXvxzd5k+AV/u4dX4rXOYy8`。GitHub 官方主机指纹已严格核验；未使用第三方镜像，未关闭主机指纹验证，未向 GitHub 推送。

这把密钥只是研究工作区读取 GitHub 上游源码的开发凭据，不是即时 AI 的用户体系或运行依赖；不再更新上游时可从 GitHub 删除。

## B. 本仓库 Git 作者身份（已解决）

用户明确要求不使用姓名或邮箱。仅在本仓库使用占位身份：

```text
即时 AI Research <instant-ai@local.invalid>
```

不修改全局 Git 身份。

## C. TrendRadar 局部依赖（已授权执行）

范围：只在 `experiments/TrendRadar-lab/.venv` 安装固定提交声明的锁定依赖，不安装 Docker/WSL/全局 Python 工具，不修改 `upstream/`。

- 已完成：101 个分发包，`.venv` 248.88 MiB，`pip check` 通过。
- 已验证：UTF-8 doctor、单一热榜 + 单一公开 RSS、两轮幂等保存、SQLite、排名历史和 HTML。
- 已保持关闭：AI、翻译、通知、S3 和 MCP。
- 已记录缺陷：GBK emoji、RSS-only 门控、新增检测矛盾和严重错误 exit 0。

## 不在本次申请范围

正式产品开发已经用户批准并进入本地 MVP；本记录不自动批准 Docker、WSL、全局 Node/Python 组件、Folo/n8n monorepo 依赖、真实模型密钥、自动交易或真实通知账号。

## D. 后续单独批准边界

- 接入真实 AI 模型前需确认服务选择并使用安全密钥存储；当前无密钥核心必须持续可用。
- Docker、WSL、全局运行时和大型依赖仍需单独批准。
- 自动交易、账户体系和多用户层不在批准范围内。
