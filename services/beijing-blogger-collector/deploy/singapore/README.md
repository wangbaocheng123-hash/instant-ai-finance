# 北京采集套件的新加坡接入候选

用于评估 `collector.grandpaamu.com` 的现有受控网关方案。应用和数据留在北京。先核对阿里云接入条件，部署时在北京直连与新加坡接入中选择一处作为公网入口。

| 公网路径 | 新加坡回环 | 北京服务 |
| --- | --- | --- |
| `/`、`/collector/`、`/health/version` | 18817 | 博主采集器 18797 |
| `/model`、登录、退出及主人认证 | 18819 | 模型下载器 8787 |

网关使用已核验的北京 SSH 主机身份。专用 systemd 单元只建立两个回环转发，沿用北京模型下载器的主人登录。渲染脚本复用北京直连候选的同一份 Caddy 路由，保留统一首页、原 API、登录转发和公开版本接口。

## 上线前检查与顺序

1. 在新加坡核对 18817 和 18819 空闲，确认既有 SSH 身份可转发到北京回环 18797、8787。运行 `systemd-analyze verify` 检查候选单元。得到生产维护授权后，独立安装并启动该单元。在新加坡读取 18817 的 `/health/version`，校对北京采集器身份和修订号，确认 18819 未登录返回原登录入口。
2. 运行 `python3 services/beijing-blogger-collector/deploy/singapore/render-collector-gateway.py` 生成 `collector-grandpaamu.caddy`。备份当前 `/etc/caddy/Caddyfile.instant-ai`，在候选包装配置末尾追加一次 `import /etc/caddy/collector-grandpaamu.caddy`。校验完整配置并核对罗盘、即时 AI 与社区的已有 import。现场 Caddy 共享配置关闭管理接口，按已有受控维护流程加载配置并核验各站点。
3. 为精确主机 `collector` 启用 A 记录 `47.236.175.118`，完成证书签发。用普通公网访问核对 `/health/version`、登录前的访问限制和主人登录后的 `/`、`/collector/`、`/model`，然后依次执行北京和即时 AI 的既定发布流程。
4. 出现故障时恢复包装文件并校验，重新加载后撤下采集网关的单元与新增 DNS 记录。保留北京服务和现场错误，复核同机其他站点。
