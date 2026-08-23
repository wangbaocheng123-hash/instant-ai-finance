# 上游研究原件

本目录下每个上游项目必须是独立 Git 仓库，保持官方远程、Git 历史和许可证。源码由根 `.gitignore` 排除，不提交到研究主仓库，不在这里直接修改。

R0 已完成 TrendRadar、RSSHub、changedetection、OpenBB、Folo 和 n8n 六个官方仓库的 `depth=1`、`filter=blob:none` 浅克隆，并按 `UPSTREAM_LOCK.yaml` 核验提交。SSH 凭据仅用于读取和可选更新这些研究原件，不属于即时 AI 产品运行环境。
