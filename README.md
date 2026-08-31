# 即时 AI

`即时 AI` 是个人使用的全球财经即时热点跟踪客户端。它持续采集公开财经文字来源，按最新程度、来源可信度、多来源关注和重要度展示全球、华尔街、中国与亚洲、黄金矿业、AI 产业链、宏观和地缘事件。

`重点事件关注`只读同步时变罗盘的首页与紫金未来时间线，保留两套来源标签。每个事件可携带已核验的发布方、北京时间/观察窗口和官方渠道；即时 AI 对到期渠道建基线并监测与事件相关的实质变化，同时把当前财经消息只标记为“候选”。官方页面实质变化会经服务器本机专用信号接口送回罗盘，由罗盘 Codex 重新联网核验后再决定是否写入 AI 指引与通知；变化信号本身不等同于结果。两套系统不共用数据库，也不会把罗盘持仓、交易或基金数据传入即时 AI。

英文新闻详情以“浏览器翻译原文”为主：iPhone 会直接切换到 Chrome 打开原站，其他设备使用默认浏览器；即时 AI 不抓取或保存新闻正文。产品内只保留“中文摘要（备用）”，翻译资讯源已经提供的短摘要，并与新闻使用相同的短期自动淘汰周期。

本项目不建立长期新闻档案：普通消息保留 72 小时，重要消息保留 5 天，最高重要度消息最多保留 7 天；新闻、临时证据、原始抓取响应、翻译和图片随后自动清理。云端只有一个主人账户和 30 天安全会话，没有注册、邮箱、个人资料、团队、多用户权限、自动交易、视频直播或付费墙绕过。

`模型先生`作为独立的单主人手机资料库位于“重点事件关注”右侧。登录后可直接播放有声压缩视频、查看或保存视频原文、读取普通/豆包识别结果、浏览评论、投资思路和受控问答；抖音原地址只作来源核验。可选现场豆包语音识别必须由所有者另行批准，以 Git 外 root 专用凭据启用，并在每次付费调用前确认。代码与业务资料严格分离：视频、评论和详情存放在 Git 外主人目录，原项目的粉丝资料、管理功能、整份 `.env`、密钥值、原始数据库、本机路径和原始 JSON 都不进入本仓库或公网接口。

`博主`是即时 AI 现有仓库内的单主人模块，入口位于模型先生右侧。北京服务器保留原模型下载器并旁路运行独立博主采集服务；采集到的作品、公开评论、作者互动和基础分类通过签名 HTTPS 主动推送到新加坡。新加坡直接复用即时 AI 已有主人登录、HTTPS 和发布链路，但博主资料使用独立 Git 外目录与 SQLite，不混入财经新闻或模型先生数据。当前传输完成后只进入 `awaiting_asr_approval`，不会自动调用豆包、ASR 或产生费用。

## 统一仓库结构

- `product/`：Python、SQLite、API、Windows 启动器和已构建静态资源；
- `client/instant-ai/`：可修改、可重新构建的前端源代码，继续遵守 AGPL-3.0-only；
- `deploy/aliyun/`：阿里云个人手机版部署与更新脚本；
- `docs/`、`research/`：架构决定与开源复用证据；
- `upstream/`、`forks/`：本机独立上游仓库，不进入本 Git 仓库。

数据库、新闻原文、证据、图片、缓存、日志、备份、Cookie 和密钥永远不进入 Git。

## 手机即时入口

- 正式在线地址：<https://grandpaamu.com/>
- 应急备用地址：<https://instant-ai.47-236-175-118.sslip.io/>
- 统一代码仓库：<https://github.com/wangbaocheng123-hash/instant-ai-finance>
- 本入口没有注册或多用户功能，只使用一个主人账户。首次登录后，本设备 30 天内无需重复登录；主动退出、清理网站数据、修改密码或会话到期后需要重新登录。
- iPhone 把 Chrome 设为默认浏览器后，用 Chrome 打开正式在线地址，再点“分享”→“添加到主屏幕”。若出现“作为网页 App 打开 / Open as Web App”，请关闭该选项；这样从主屏幕进入后仍保留 Chrome 页面翻译。0.9.0 及更早建立的旧图标需要先删除再重新添加一次。`www.grandpaamu.com` 会自动跳转到正式根域名。
- 点击“浏览器翻译原文”后，iPhone 直接切换到 Chrome；在 Chrome 对英文页面选择“翻译”，并可开启“始终翻译英语”。页面同时保留“普通浏览器备用打开”；来源登录、付费墙和地区限制仍由原站决定。
- 云端每 5 分钟自动采集；已打开的页面每 60 秒自动刷新，重新打开、回到前台或恢复网络时立即刷新。API 明确禁止缓存，主屏幕版本不会把旧接口数据离线缓存起来。

阿里云部署采用只新增方式：程序位于 `/opt/instant-ai/repository`，独立运行数据位于 `/var/lib/instant-ai`；主人凭据、模型先生详情与压缩媒体均保存在 Git 之外，没有覆盖服务器原有站点或原有 Caddy 配置文件。

## Windows 开发与验证

```powershell
cd client\instant-ai
npm ci
npm run build
cd ..\..
@('app.js','styles.css','index.html','manifest.webmanifest','sw.js','app-icon.svg') | ForEach-Object {
  Copy-Item (Join-Path 'client\instant-ai\dist' $_) (Join-Path 'product\instant_ai\static' $_) -Force
}
Push-Location product
python -m unittest discover -s tests -v
Pop-Location
```

桌面客户端启动：

```powershell
pythonw product\launch_instant_ai.py
```

## 统一更新规则

`main` 是唯一可部署分支。家中电脑、云端服务器和其他 Codex 环境都先从远程仓库拉取 `main`，修改后通过分支和提交回传，再由 `main` 发布。用户明确要求正式发布后，云端 Codex 先运行 `deploy/aliyun/check-publish-channel.sh`，再使用 `sudo -n /usr/local/sbin/instant-ai-publish` 自动完成无数据同步更新、服务器测试、服务重启和健康检查；不需要用户粘贴服务器命令。底层脚本为 `deploy/aliyun/update-instant-ai.sh`，只供 root 安装或修复发布通道。H 盘和云端运行数据不会互相复制。

前端源代码与 World Monitor 的来源、许可证和固定提交见 `client/instant-ai/NOTICE.txt`。完整许可证见根目录 `LICENSE`。
