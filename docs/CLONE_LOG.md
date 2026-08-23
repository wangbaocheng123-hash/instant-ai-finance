# R0 上游下载与克隆日志

## 2026-08-23 — TrendRadar

目标：`https://github.com/sansan0/TrendRadar.git`

尝试 1：

```text
git clone --depth 1 --filter=blob:none
结果：RPC failed; curl 28 Recv failure: Connection was reset
```

尝试 2：同一官方 URL，结果再次为 `Connection was reset`。

尝试 3：

```text
git -c http.version=HTTP/1.1 ls-remote --symref ... HEAD
结果：Failed to connect to github.com port 443
```

只读替代通路检查：GitHub 官方 REST API `api.github.com` 可访问；官方仓库归档重定向到 `codeload.github.com`。在确认无法使用官方 Git 克隆通路前，不把归档下载冒充轻量克隆，也不使用第三方镜像或 Fork。

当前状态：`OFFICIAL_SHALLOW_CLONES_COMPLETE`。

## 官方通路诊断结论

- `github.com:443` TCP 不通；HTTPS Git 默认和强制 HTTP/1.1 均失败。
- 未发现环境代理、Git 代理或 WinHTTP 代理。
- `api.github.com` 与 `codeload.github.com` 可访问。
- `github.com:22` 与 `ssh.github.com:443` 的 SSH 握手可达，但当前没有 SSH Agent、默认私钥或 `gh`，认证为 `Permission denied (publickey)`。
- 完整浅克隆的官方可行路径是：用户批准创建 GitHub SSH 密钥、验证官方主机指纹、把公钥登记到 GitHub 账户后，通过 SSH 进行 `--depth 1 --filter=blob:none` 克隆。

用户要求其余任务继续后，已创建专用密钥 `C:\Users\36590\.ssh\id_ed25519_jishi_ai`；公钥指纹为 `SHA256:YBMC0KNFrzWoxjNpWZoMaXvxzd5k+AV/u4dX4rXOYy8`。用户已于 2026-08-23 手工把公钥登记到 GitHub。私钥未进入仓库或对话。

连接使用 `ssh.github.com:443`。实际扫描到的 Ed25519 主机指纹为 `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`，与 GitHub 官方文档完全一致；指纹已固定在独立的 `known_hosts_jishi_ai` 文件中。始终启用严格主机校验，未使用第三方镜像，也未推送任何内容。

该 SSH 密钥仅用于 R0 获取和日后可选更新 GitHub 官方上游源码，不属于即时 AI 产品的账户或运行依赖。若不再需要更新上游，用户可在 GitHub 删除对应公钥。

## 固定提交归档快照

为继续静态分析，已通过 GitHub 官方 API/codeload 下载六个固定提交归档。完整提交、归档 SHA-256、大小和解压路径见 `docs/SNAPSHOT_MANIFEST.md`。这些快照明确标记为 `OFFICIAL_ARCHIVE_SNAPSHOT`，不含 `.git`，不计为克隆完成。

n8n 原始归档在 Windows 下无法创建 `.claude/plugins/...` 的符号链接；静态分析副本排除了 `.claude` 开发辅助目录，原始归档保持不变并有 SHA-256 记录。

## 正式浅克隆结果

六仓均使用官方 SSH 地址、`--depth 1 --filter=blob:none --no-tags --single-branch` 完成；每仓 `rev-list --count HEAD` 均为 1，`rev-parse --is-shallow-repository` 均为 `true`。

| 项目 | 锁定提交 | 工作树与浅层 Git 大小 |
|---|---|---:|
| TrendRadar | `8ee26026ba6c11dec41a95fb3895a7162876caa1` | 23.91 MiB |
| RSSHub | `5151c3233bc7bacfaecc6e4f01aba2b60022d683` | 22.37 MiB |
| changedetection | `fce24780e74199bf34c62a0d90188cc2fc12f061` | 20.17 MiB |
| OpenBB | `3e071fcc2cd9f891cac6040ae60296dba76dab46` | 339.67 MiB |
| Folo | `7c220c69a841defbfeeb00a86ed75ad482b22a57` | 26.36 MiB |
| n8n | `7968432083cdc2526b3b08983d84d0dc73176356` | 254.08 MiB |

合计约 686.56 MiB；六个独立 `.git` 仓库与上游许可证均保留，且全部被研究主仓库 `.gitignore` 排除。
