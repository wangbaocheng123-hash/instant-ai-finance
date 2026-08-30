# 即时 AI 阿里云个人手机版部署说明

此目录把统一 Git 仓库中的同一套公开财经来源采集程序部署为个人云端副本。H 盘运行数据及其短期证据、备份、缓存和日志不得上传到云端；云端自己采集并按 ADR-0010 自动淘汰。

## 固定部署布局

- Git 仓库：`/opt/instant-ai/repository`
- 程序：`/opt/instant-ai/repository/product`
- 云端独立数据：`/var/lib/instant-ai`
- systemd：`/etc/systemd/system/instant-ai.service`
- 独立 Caddy 配置：`/etc/caddy/instant-ai.caddy` 与 `/etc/caddy/Caddyfile.instant-ai`
- Caddy 增量加载：`/etc/systemd/system/caddy.service.d/instant-ai.conf`（不改原 `Caddyfile`）

Python 服务只监听云服务器自己的 `127.0.0.1:18765`。公网只通过现有 Caddy 的 HTTPS 进入，不增加注册、邮箱或多用户体系；业务接口由一个主人账户和 30 天安全会话保护。主人凭据保存在 `/var/lib/instant-ai/auth.json`，不进入 Git。

正式手机入口为 `https://grandpaamu.com/`；`https://www.grandpaamu.com/` 永久跳转到根域名。原 `sslip.io` 地址继续作为服务器公网 IP 不变时的应急入口。`grandpaamu.com` 的根记录与 `www` 记录都必须指向当前即时 AI 云服务器公网 IP。

## 部署闸门

执行远程安装前必须先确认：

1. 使用用户明确选中的既有 ECS 或轻量应用实例，不自动购买资源；
2. 已有可用域名及 HTTPS 证书，或由用户明确选择其他安全入口；
3. 先只读盘点现有目录、服务、监听端口、Nginx 站点和证书；
4. 不覆盖、删除、重命名该实例上的任何已有文件、站点、服务或 Nginx 配置；目标路径已存在时立即停止；
5. 只新增 `/opt/instant-ai`、`/var/lib/instant-ai`、`instant-ai.service`、独立 Caddy 站点与 systemd drop-in；原 Caddyfile 保持不变；
6. 阿里云安全组只开放 HTTPS 所需端口，不开放 `18765`。

## 主人账户与模型先生主人资料库

代码发布完成后，首次启用主人账户应在 Alibaba Cloud Client 的服务器终端以 root 运行：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/configure-instant-ai-owner.sh --generate owner
```

密码在服务器端随机生成并只显示一次；配置文件只保存 `scrypt` 哈希和随机会话密钥。也可以省略 `--generate` 后交互输入两次密码，密码不会进入 Shell 历史。

模型先生主人资料库默认位于 `/var/lib/instant-ai/model-mr`：`public-snapshot.json` 为作品索引，`details/` 保存每条作品的正式原文、白名单转写和评论正文，`media/` 保存 360p H.264 + AAC 视频。它们都是 Git 外运行数据，所有 API 和视频分段请求必须先通过主人登录；Caddy 不得直接暴露 `media/`。

本机使用 `scripts/export-model-mr-owner-library.py` 从回环服务生成索引与详情，媒体源为 `H:\模型先生智能体\模型视频_360p_有声`。首次同步传全部 388 个 MP4；后续按相对路径增量覆盖新增或变化文件，不重复提交 Git。不得上传模型先生的整份 `.env`、原始数据库、粉丝资料、本机绝对路径、原始 JSON、Cookie、日志或管理接口。完整边界见 ADR-0022。

云端默认只读取已有普通/豆包识别结果。若所有者另行批准云端付费识别，可在服务器端单独提供豆包语音凭据到固定暂存文件 `/var/tmp/instant-ai-doubao.env.upload`，再由 root 运行 `deploy/aliyun/configure-model-mr-doubao.sh`；脚本只接受豆包语音白名单字段，最终配置保存在 Git 外 `/etc/instant-ai/model-mr-secrets.env`（root `0600`）。页面每次现场调用豆包前都会提示按音频时长计费。不得把该文件、值或模型先生整份 `.env` 提交 Git、聊天、日志或公开接口。

## 统一更新

服务器首次从统一远程仓库克隆 `main` 到 `/opt/instant-ai/repository`。云端 Codex 在用户明确要求正式发布后，先执行只读检查：

```bash
deploy/aliyun/check-publish-channel.sh
```

检查输出 `CODEX_CLOUD_PUBLISH_READY` 后，由 Codex 直接运行既有窄权限发布器：

```bash
sudo -n /usr/local/sbin/instant-ai-publish
```

不需要用户进入 root 终端或粘贴命令。不得以 `sudo -n true` 判断这个通道是否存在；它只会测试未授权的通用 root。需要排查时使用 `sudo -n -l` 查看精确白名单。

只有在安装或修复发布器的运维场景，root 操作员才直接运行底层脚本：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/update-instant-ai.sh
```

发布器是底层脚本的 root 持有副本，只接受 fast-forward 更新；云端若有其他 Codex 尚未提交的修改会立即停止，避免覆盖。它在服务器运行项目 Python 测试，随后只重启 `instant-ai.service` 并验证回环健康。业务数据目录 `/var/lib/instant-ai` 不属于 Git，也不会随代码更新删除或上传。

首次建立或需要修复发布通道时，由 root 在目标服务器运行一次：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/install-codex-publish-channel.sh
```

安装器只写入 root 持有的 `/usr/local/sbin/instant-ai-publish` 和 `/etc/sudoers.d/compassdev-instant-ai-publish`，并用 `visudo` 检查；不会授予其他 sudo 命令。时变罗盘使用自己的独立发布器，不由本安装器修改。

首次部署在确认全部目标路径空闲后运行：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/install-instant-ai-additive.sh <HTTPS主机名>
```

安装器先在临时目录验证“原 Caddyfile + 新站点”的组合，再新增独立文件并原子 reload；同时比较原 Time Compass 本地健康状态，目标已存在时拒绝覆盖。

部署完成后，在 iPhone Chrome 中打开 HTTPS 地址并完成一次访问验证；随后可使用“添加到主屏幕”。若系统显示“作为网页 App 打开 / Open as Web App”，可关闭该选项以保留浏览器界面；旧 standalone 图标必须删除后重新添加。0.9.2 起，“浏览器翻译原文”在 iPhone 上通过 Chrome 官方 URL scheme 直接切换到 Chrome，同时保留普通 HTTPS 备用入口。API 不进入离线缓存；页面每 60 秒读取最新数据，并在 iPhone 从后台恢复、重新联网或重新打开时立即刷新；后端每 5 分钟采集新消息。完整原文只由浏览器打开和翻译，云端即时 AI 不抓取正文，只保留短期摘要备用。
