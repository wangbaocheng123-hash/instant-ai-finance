# R0 下载与磁盘占用

- 测量日期：2026-08-23
- H 盘官方固定提交归档：190.74 MiB
- C 盘六个可分析源码快照合计：476.28 MiB
- C 盘六个正式官方浅克隆合计：686.56 MiB；每仓均只有一个提交，保留独立 `.git` 与官方远程
- TrendRadar 实验目录合计 265.81 MiB：`.venv` 248.88 MiB、101 个分发包，实验输出 6.89 MiB，另含源码副本
- 研究 Markdown：0.43 MiB
- 实际安装：仅 TrendRadar 项目局部依赖；其他五仓依赖为 0
- `node_modules`：未创建
- Docker/WSL：未安装或配置

末次测量：C 盘可用 8.96 GiB；H 盘可用 157.15 GiB。

归档逐项大小、文件数和 SHA-256 见 `SNAPSHOT_MANIFEST.md`。正式浅克隆、源码快照和实验源码/虚拟环境均被 `.gitignore` 排除，不进入研究主仓库提交。

注意：OpenBB 与 n8n 的源码快照体积占主要部分；若继续安装 TrendRadar、OpenBB、Folo 或 n8n 依赖，必须先评估下载量和 C/H 盘落点并获得用户批准。
