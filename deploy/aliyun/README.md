# 即时 AI 阿里云个人手机版部署说明

此目录把统一 Git 仓库中的同一套公开财经来源采集程序部署为个人云端副本。H 盘运行数据及其短期证据、备份、缓存和日志不得上传到云端；云端自己采集并按 ADR-0010 自动淘汰。

## 固定部署布局

- Git 仓库：`/opt/instant-ai/repository`
- 程序：`/opt/instant-ai/repository/product`
- 云端独立数据：`/var/lib/instant-ai`
- systemd：`/etc/systemd/system/instant-ai.service`
- Nginx：`/etc/nginx/conf.d/instant-ai.conf`
- 访问口令文件：`/etc/nginx/instant-ai.htpasswd`

Python 服务只监听云服务器自己的 `127.0.0.1:18765`。公网只能通过 Nginx 的 HTTPS 和服务器级访问口令进入，不在产品中增加注册、邮箱或多用户账户。

## 部署闸门

执行远程安装前必须先确认：

1. 使用用户明确选中的既有 ECS 或轻量应用实例，不自动购买资源；
2. 已有可用域名及 HTTPS 证书，或由用户明确选择其他安全入口；
3. 不覆盖该实例上已有站点和 Nginx 配置；
4. 访问用户名和口令只在服务器上生成，不写进仓库或部署包；
5. 阿里云安全组只开放 HTTPS 所需端口，不开放 `18765`。

## 统一更新

服务器首次从统一远程仓库克隆 `main` 到 `/opt/instant-ai/repository`。以后运行：

```bash
sudo /opt/instant-ai/repository/deploy/aliyun/update-instant-ai.sh
```

脚本只接受 fast-forward 更新；云端若有其他 Codex 尚未提交的修改会立即停止，避免覆盖。业务数据目录 `/var/lib/instant-ai` 不属于 Git，也不会随代码更新删除或上传。

部署完成后，在手机浏览器打开 HTTPS 地址并完成一次访问验证；随后可使用“添加到主屏幕”，得到独立的“即时 AI”图标和接近客户端的全屏打开方式。
