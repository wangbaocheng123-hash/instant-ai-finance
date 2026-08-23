# R0 官方源码快照清单

- 获取日期：2026-08-23
- 来源：GitHub 官方 REST API，重定向至 `codeload.github.com`
- 用途：在 Git HTTPS 克隆受阻时继续静态源码分析
- 限制：归档不含 `.git`、远程地址和历史，不能视为完成 `R0-CLONE-01`

| 项目 | 固定提交 | 归档 MiB | 解压 MiB | 文件数 | SHA-256 |
|---|---|---:|---:|---:|---|
| TrendRadar | `8ee26026ba6c11dec41a95fb3895a7162876caa1` | 8.46 | 15.27 | 162 | `0ADD46EBDB71A0586687DFCE4C09E5172FE2D09A11ED917C11E6CA39815FF6CF` |
| RSSHub | `5151c3233bc7bacfaecc6e4f01aba2b60022d683` | 3.41 | 15.10 | 6,805 | `B6A16A9A5D7C5686F83A667A43BBCE0611770DAFBA4219792BC49F86E36E1634` |
| changedetection.io | `fce24780e74199bf34c62a0d90188cc2fc12f061` | 5.29 | 14.65 | 1,111 | `46FBD0930C8A44578C5BCBC53E9B360723669A697156BBEEA0D9931FE3955802` |
| OpenBB | `3e071fcc2cd9f891cac6040ae60296dba76dab46` | 115.82 | 228.31 | 2,191 | `36D66023F391B42031EFFAA853D6FD58855DF71D98A1BEFCCDAFAD0C6FC5ECF4` |
| Folo | `7c220c69a841defbfeeb00a86ed75ad482b22a57` | 7.69 | 17.27 | 3,418 | `EA6661B150339412D665E9ECEE45A3CF7E25D0BA87B8DB3621621FED30CBE9AB` |
| n8n | `7968432083cdc2526b3b08983d84d0dc73176356` | 50.09 | 185.68 | 27,108 | `1B0ECF55F483D8F426B7AAF3E4C03501F63C4570331902483CEA3963358577D5` |

归档缓存在 `H:\即时AI文件库\cache\upstream-archives`。前五个项目解压到 `upstream/<项目>-snapshot`。n8n 因 Windows 无法创建仓库 `.claude` 下的符号链接，静态研究使用 `upstream/n8n-snapshot-usable`，仅排除了 `.claude` 开发辅助目录；原始归档哈希完整保留。

## Git 克隆阻塞

`github.com:443` 的 Git HTTPS 连接不可达；官方 API/codeload 可达。`github.com:22` 和 `ssh.github.com:443` 的 SSH 握手可达，但当前没有 GitHub SSH 凭据。只有用户批准创建并登记 SSH 密钥后，才能完成保留浅层 Git 历史的正式克隆。

