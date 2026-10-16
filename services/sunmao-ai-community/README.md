# 榫卯 AI 社区

这是 `instant-ai-finance` 公共仓库中的独立应用，不是北京博主采集器的页面或子模块。

它与北京博主采集器同属一个 Git 仓库，但始终是并列的独立应用，不并入即时 AI、时变罗盘、北京博主采集器或模型下载器：

- 社区源码：`services/sunmao-ai-community/`
- 社区进程：`sunmao-ai-community.service`
- 回环端口：`127.0.0.1:18808`
- 运行数据库：`/var/lib/sunmao-community/community.sqlite3`
- 原子发布目录：`/opt/sunmao-community/`
- 生产分支：`sunmao-production`
- 社区精确公网域名：`https://amuyeye.com/`、`https://阿木爷爷.com/`。中文根域名继续由北京 Caddy 直连 18808；英文根域名保留在新加坡，通过仅监听 `127.0.0.1:18818` 的 SSH 隧道转发到北京同一个 18808 服务，因此两边共用交流和统计数据。不得接管 `www.amuyeye.com`、`mcp.amuyeye.com` 或创建 `*.amuyeye.com` 通配符。

公共页面顶部固定显示“尺寸型 / 交流区 / 榫卯108道”三项导航；尺寸型通过`web/size-resources.json`按顺序展示经过来源白名单校验的外部资料卡；交流区的中央大屏显示公开问题、咨询、经验和意见，底部固定输入栏只填写纯文字内容，不要求访客设置昵称。切换到尺寸型或榫卯108道时，底部输入栏隐藏。手机输入框保持至少16px字号，不通过禁用页面缩放来规避浏览器聚焦放大。

## 云端知识库规划

后续正式知识资料由北京社区服务从自己的 Git 外 `/var/lib/sunmao-community` 数据目录提供，用户访问不依赖所有者电脑。尺寸和检索元数据进入现有社区 SQLite；图纸、模型、图片及有权托管的自有媒体进入 `knowledge/` 资源目录，第三方视频和文章保留原链接。云端目录、资料字段、审核发布、备份与迁移契约见 `docs/CLOUD_KNOWLEDGE_LIBRARY.md` 和 ADR-0076。0.3.9开发候选已开始实施统一知识表、图片原件目录和公开只读资源接口；受保护上传/审核、通用检索、备份恢复演练、AI问答与生产发布仍未完成。

尺寸型的外部文章继续以 `web/size-resources.json` 作为经审核的Git种子清单，服务启动后同步到社区SQLite。经所有者确认允许托管的图片作品不进入Git：原图写入 `/var/lib/sunmao-community/knowledge/originals/images/<asset-id>/`，元数据写入统一`knowledge_*`表，再通过不暴露文件路径的同源资源ID读取。服务器侧导入命令为：

```bash
python3 -m scripts.import_size_gallery manifest.json
```

清单必须包含真实标题、来源URL、说明、显示顺序和本地原图列表；导入器只接受JPEG、PNG、GIF、WebP，每张不超过20 MiB，最多40张。微信或抖音验证页无法证明的标题、正文和图片不得用猜测内容发布。

所有者从手机向新加坡云端 Codex 提供新资料时，执行本目录 `AGENTS.md` 和 `docs/KNOWLEDGE_INTAKE_RUNBOOK.md`；分类代码见 `config/knowledge_taxonomy.v1.json`，约束见 ADR-0077。分类按“一个主题、多种资料形式”，避免把同一榫卯的尺寸、模型和视频拆成不相关的条目。

北京博主采集器继续使用自己的目录、`blogger-collector.service`、端口 `18797`、数据目录和 `beijing-production`；新加坡即时 AI、时变罗盘和 `mcp.amuyeye.com` 也继续使用各自的服务、端口、配置和数据。发布社区不得重启或改写这些并列应用。

## 本地运行

```powershell
cd services/sunmao-ai-community
python -m unittest discover -s tests -p "test_*.py"
python -m sunmao_community --host 127.0.0.1 --port 4173 --database .runtime/community.sqlite3
```

## 发布规则

普通 `main` 推送只保存开发检查点，不上线。所有者明确说“更新并发布榫卯AI社区：<具体需求>”后，才允许把已验收提交安全快进到 `sunmao-production`，再触发固定的一次性 `sunmao-community-git-deploy.service`。该发布只切换 `/opt/sunmao-community/current` 并重启社区进程。

社区已经从早期“由采集器代为提供页面”的临时拓扑迁移为独立服务。以后普通社区功能发布只更新社区 release 和进程，不修改 Caddy 或并列应用。

## 管理后台与访问统计

首页顶部右侧显示“管理后台”按钮，进入 `/admin/` 单主人账号和密码登录页；后台可查看今日与累计匿名访客、访问次数、浏览量、趋势、栏目、来源域名、设备和最近访客。系统只保存浏览器随机标识的摘要，不保存 IP 地址、设备指纹或完整 User-Agent。随机标识以一年期第一方 Cookie 为主，并由同源浏览器本地存储备份；Cookie 丢失后可恢复同一访客名称，但清除全部站点数据、无痕窗口、换浏览器、换设备或换域名仍视为新访客。

后台默认关闭。生成密码摘要：

```powershell
python -m scripts.generate_admin_password_hash
```

生产环境把单主人账号以 `0600` 权限保存到 `/var/lib/sunmao-community/admin-username`，把生成器输出的密码摘要保存到同权限的 `/var/lib/sunmao-community/admin-password.hash`；两者属主均为 `sunmaocommunity:sunmaocommunity`，不得写进 Git、日志或聊天。只有账号和密码同时匹配才建立后台会话。详细演进顺序见 `docs/ADMIN_AND_MONETIZATION_PLAN.md`。

## 从独立仓库回到原仓库

独立仓库 `wangbaocheng123-hash/sunmao-ai-community` 只作为迁移历史和只读来源，不再承担日常开发或发布。一次性回切使用 `deploy/beijing/rejoin-original-repository.sh`：它会保留当前 release、数据库、后台密码摘要和 Caddy，只把社区裸仓库与固定发布器恢复为本仓库的 `sunmao-production` 子树发布，并把旧裸仓库和修订状态移入可恢复备份。

回切脚本会停止独立仓库的 GitHub Webhook path 触发器并保持所有 Git timer 关闭；它不重启社区、不发布新版本，也不停止或重启北京博主采集器。回切验收后，仍需所有者明确发布，随后只执行一次 `systemctl start --no-block sunmao-community-git-deploy.service`。

新加坡服务器上的 Codex 继续作为日常操作端：修改本仓库、运行测试、推送 `main`，收到明确发布指令后推进 `sunmao-production`，再经既有 `/home/compassdev/bin/beijing-admin` 在北京启动社区发布器。它不需要把社区部署到新加坡，也不需要每分钟轮询 Git。

`deploy/singapore/`保存英文根域名入口的可复现模板。新加坡不运行第二份社区应用或数据库，只运行低权限SSH本地转发和一个精确根域名Caddy块；`mcp.amuyeye.com`、即时AI与`compass.grandpaamu.com`的现有配置不得并入或改写。根域名过去由时变罗盘使用，因此Caddy额外提供无缓存的`/service-worker.js`清理脚本，使旧浏览器逐步退出罗盘离线缓存；时变罗盘正式入口继续是`https://compass.grandpaamu.com/`。
