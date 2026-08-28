# 即时 AI 阿里云个人手机版部署说明

此目录把统一 Git 仓库中的同一套公开财经来源采集程序部署为个人云端副本。H 盘运行数据及其短期证据、备份、缓存和日志不得上传到云端；云端自己采集并按 ADR-0010 自动淘汰。

## 固定部署布局

- Git 仓库：`/opt/instant-ai/repository`
- 程序：`/opt/instant-ai/repository/product`
- 云端独立数据：`/var/lib/instant-ai`
- systemd：`/etc/systemd/system/instant-ai.service`
- 独立 Caddy 配置：`/etc/caddy/instant-ai.caddy` 与 `/etc/caddy/Caddyfile.instant-ai`
- Caddy 增量加载：`/etc/systemd/system/caddy.service.d/instant-ai.conf`（不改原 `Caddyfile`）

Python 服务只监听云服务器自己的 `127.0.0.1:18765`。公网只通过现有 Caddy 的 HTTPS 进入，不增加注册、邮箱、账户或密码。该地址只允许展示公开、可重建、短周期财经消息；任何知道地址的人都可以访问。

正式手机入口为 `https://grandpaamu.com/`；`https://www.grandpaamu.com/` 永久跳转到根域名。原 `sslip.io` 地址继续作为服务器公网 IP 不变时的应急入口。`grandpaamu.com` 的根记录与 `www` 记录都必须指向当前即时 AI 云服务器公网 IP。

## 部署闸门

执行远程安装前必须先确认：

1. 使用用户明确选中的既有 ECS 或轻量应用实例，不自动购买资源；
2. 已有可用域名及 HTTPS 证书，或由用户明确选择其他安全入口；
3. 先只读盘点现有目录、服务、监听端口、Nginx 站点和证书；
4. 不覆盖、删除、重命名该实例上的任何已有文件、站点、服务或 Nginx 配置；目标路径已存在时立即停止；
5. 只新增 `/opt/instant-ai`、`/var/lib/instant-ai`、`instant-ai.service`、独立 Caddy 站点与 systemd drop-in；原 Caddyfile 保持不变；
6. 阿里云安全组只开放 HTTPS 所需端口，不开放 `18765`。

## 统一更新

服务器首次从统一远程仓库克隆 `main` 到 `/opt/instant-ai/repository`。以后运行：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/update-instant-ai.sh
```

脚本只接受 fast-forward 更新；云端若有其他 Codex 尚未提交的修改会立即停止，避免覆盖。业务数据目录 `/var/lib/instant-ai` 不属于 Git，也不会随代码更新删除或上传。

首次部署在确认全部目标路径空闲后运行：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/install-instant-ai-additive.sh <HTTPS主机名>
```

安装器先在临时目录验证“原 Caddyfile + 新站点”的组合，再新增独立文件并原子 reload；同时比较原 Time Compass 本地健康状态，目标已存在时拒绝覆盖。

部署完成后，在 iPhone 默认 Chrome 中打开 HTTPS 地址并完成一次访问验证；随后可使用“添加到主屏幕”。若系统显示“作为网页 App 打开 / Open as Web App”，关闭该选项，使入口以浏览器模式运行并可使用 Chrome 翻译；旧 standalone 图标必须删除后重新添加。API 不进入离线缓存；页面每 60 秒读取最新数据，并在 iPhone 从后台恢复、重新联网或重新打开时立即刷新；后端每 5 分钟采集新消息。完整原文只由默认浏览器打开和翻译，云端即时 AI 不抓取正文，只保留短期摘要备用。
