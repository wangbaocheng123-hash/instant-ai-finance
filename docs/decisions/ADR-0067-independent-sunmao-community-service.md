# ADR-0067：榫卯 AI 社区与北京博主采集器并列部署

- 日期：2026-10-13
- 状态：已采纳；2026-10-13完成首次生产迁移与公网验收
- 社区目标版本：`0.2.0`
- 替代范围：ADR-0065第4—6项、ADR-0066第5—8项中的从属运行与发布拓扑

## 背景

首版为了快速上线，社区静态页临时由北京博主采集器的`/community/*`路径提供；第二阶段最初也错误地把交流API和SQLite接入采集器，并拟用采集器`1.3.0`与`beijing-production`发布。所有者明确纠正：榫卯AI社区和北京博主采集器是两个并列应用，不是采集器功能。它们只是在同一Git仓库和同一台北京服务器上共存。

## 决定

1. 社区源码固定在`services/sunmao-ai-community/`，采集器源码中不保留社区页面、消息API、数据库配置或社区版本号。
2. 社区使用`sunmao-ai-community.service`，只监听`127.0.0.1:18808`；采集器继续使用`blogger-collector.service`和`127.0.0.1:18797`。任何一方发布不得重启另一方。
3. 社区运行根为`/opt/sunmao-community/current`，消息库为`/var/lib/sunmao-community/community.sqlite3`；不得使用`/opt/blogger-agent`或`/var/lib/blogger-agent`。
4. `阿木爷爷.com`的Caddy站点直接代理`127.0.0.1:18808`，不再重写到采集器`/community/*`。首次迁移先启动并健康检查新服务，再以备份、校验和失败恢复脚本切换Caddy。
5. 同一公开Git仓库继续满足所有者“不新建仓库”的要求，但社区生产分支固定为`sunmao-production`，采集器仍为`beijing-production`。普通`main`推送都不自动上线，服务器不启用Git轮询timer。
6. 所有者说“更新并发布榫卯AI社区：<具体需求>”才授权社区发布；这不授权推进`beijing-production`。首次迁移还须明确包含安装独立systemd单元、固定发布器和切换Caddy。
7. 社区固定发布器只接受社区子树，隔离运行测试、原子切换`current`并重启社区服务；其权限范围不得覆盖采集器、模型下载器或即时AI数据。

## 迁移与回滚

迁移前公网首版在采集器`1.2.0`上继续运行，直到独立通道安装且新服务回环健康。迁移按“创建/验收新服务 → 备份并切换Caddy → 公网验收”顺序执行；失败时恢复Caddy备份，旧采集器页面仍可继续提供首版。迁移现已完成，后续可通过采集器自身正常版本去除历史静态路径，不为社区交叉重启两个服务。

## 验收

本地须验证社区15项以上存储、HTTP、页面和部署契约，并验证采集器自身重点回归继续通过。生产须分别确认：社区版本接口回报`sunmao-ai-community`和目标提交；采集器版本、进程和`beijing-production`未变化；公网发帖、限流、重启持久化、三入口及34条跳转正常；两项Git timer均保持关闭。

## 2026-10-13生产执行与验收记录

- 授权：所有者明确要求首次独立迁移发布，并针对本次北京轻量服务器命令助手给出网页控制例外、重试及代点击授权。未将该例外扩展至其他实例或后续任务。
- 初次发布器在Bash同句`local`声明引用尚未赋值的`source_dir`处失败；`ed01937`拆开声明并新增回归。GitHub下载不稳定期间缺少的两个blob通过官方GitHub对象接口读取，核对对象SHA后补齐；最终执行常规固定发布器，不绕过版本、隔离测试或健康检查。
- 安装并启动发布器的执行`t-bj06zw22vzmyhog`退出0；后续`t-bj06zw261rmycxs`确认发布器`Result=success`、`ExecMainStatus=0`。发布器为oneshot，结束后inactive属正常；社区应用为active。
- 社区回环与公网版本均为`sunmao-ai-community` / `0.2.0` / `ed01937e317cdd5142f25e6c6152ff7fa85df64d`，部署时间`2026-10-13T06:12:44Z`。
- Caddy切换执行`t-bj06zw29iwhc1kw`于北京时间14:14:46—14:14:47退出0，备份`/etc/caddy/backups/Caddyfile.20261013T061446Z`。校验及reload成功，`peer_processes_unchanged=true`，社区、采集器、模型下载器、Caddy均active。
- 公网POST返回201，立即重复POST返回429、`Retry-After: 15`；`t-bj06zw2ja4quuio`于14:17:48—14:17:49退出0，确认实际重启社区后合成留言仍在且并列应用InvocationID未变。验收后只把精确测试ID的`is_visible`设为0，可恢复，没有删除用户内容。
- 采集器版本仍为`blogger-collector` / `1.2.0` / `10fdb3a313f8851c8e7963fb29bc2935dfb164ae`，Git远端`beijing-production`仍指向同一提交；`sunmao-production`精确指向`ed01937`。采集器timer为disabled/inactive，社区timer未安装（LoadState=not-found），不存在Git轮询。
- 本地16项测试通过。生产浏览器默认交流区、尺寸型占位与34条视频链接均正常；首末链接为`7143069642955738408`与`7684469334751644970`，34条均新窗口且noopener。390px外框下可用内容宽375px，scrollWidth同为375px，底部三项导航无越界；非iPhone真机测试。浏览器无已捕获的错误或警告。
- 交流仍为纯文字公开区，尺寸资料待主人提供，不新增账号、AI问答或自动视频同步。应用已交付，不以这些未来功能阻塞本次发布。
