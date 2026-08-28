# 即时 AI

`即时 AI` 是个人使用的全球财经即时热点跟踪客户端。它持续采集公开财经文字来源，按最新程度、来源可信度、多来源关注和重要度展示全球、华尔街、中国与亚洲、黄金矿业、AI 产业链、宏观和地缘事件。

本项目不建立长期新闻档案：普通消息保留 72 小时，重要消息保留 5 天，最高重要度消息最多保留 7 天；新闻、临时证据、原始抓取响应、翻译和图片随后自动清理。没有产品注册、邮箱、团队、多用户权限、自动交易、视频直播或付费墙绕过。

## 统一仓库结构

- `product/`：Python、SQLite、API、Windows 启动器和已构建静态资源；
- `client/instant-ai/`：可修改、可重新构建的前端源代码，继续遵守 AGPL-3.0-only；
- `deploy/aliyun/`：阿里云个人手机版部署与更新脚本；
- `docs/`、`research/`：架构决定与开源复用证据；
- `upstream/`、`forks/`：本机独立上游仓库，不进入本 Git 仓库。

数据库、新闻原文、证据、图片、缓存、日志、备份、Cookie 和密钥永远不进入 Git。

## Windows 开发与验证

```powershell
cd client\instant-ai
npm ci
npm run build
cd ..\..
Copy-Item client\instant-ai\dist\app.js product\instant_ai\static\app.js -Force
Copy-Item client\instant-ai\dist\styles.css product\instant_ai\static\styles.css -Force
python -m unittest discover -s product\tests -v
```

桌面客户端启动：

```powershell
pythonw product\launch_instant_ai.py
```

## 统一更新规则

`main` 是唯一可部署分支。家中电脑、云端服务器和其他 Codex 环境都先从远程仓库拉取 `main`，修改后通过分支和提交回传，再由 `main` 发布。云服务器使用 `deploy/aliyun/update-instant-ai.sh` 做无数据同步更新；H 盘和云端运行数据不会互相复制。

前端源代码与 World Monitor 的来源、许可证和固定提交见 `client/instant-ai/NOTICE.txt`。完整许可证见根目录 `LICENSE`。
