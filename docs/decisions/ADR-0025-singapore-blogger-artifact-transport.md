# ADR-0025：新加坡博主 artifact 传输与付费处理闸门

- 状态：`ACCEPTED`
- 日期：2026-08-30
- 决策者：产品所有者明确要求继续实施北京到新加坡传输第二阶段

## 背景

> 2026-08-31 补充：本接收端属于即时 AI 现有仓库和现有服务，不建立新的新加坡仓库、站点或第二套 AI；北京既有模型下载器继续独立运行。部署边界详见 ADR-0026。

ADR-0024 只冻结并验证了 `blogger-transfer/v1` 清单、七行 HMAC、nonce 防重放和作品 revision 账本，没有开放 HTTP、接收媒体/评论正文或创建处理任务。北京权威发送器现已实现清单、整文件 artifact 与 complete 三段式投递，并会严格校验新加坡逐字段回执；新加坡必须补齐真实传输闭环，同时继续把“传输完成”与豆包/AI 处理隔开。

## 决定

1. 在现有回环 Python 服务中加入独立机器入口：`POST /internal/v1/transfers`、`PUT /internal/v1/transfers/{transfer_id}/media/{media_id}`、`PUT /internal/v1/transfers/{transfer_id}/comments/{bundle_id}` 和 `POST /internal/v1/transfers/{transfer_id}/complete`。这些路由在主人 Cookie 与 `X-Instant-AI` 判断之前分流，但只认北京七行 HMAC；主人身份不能代替机器签名，机器 HMAC 也绝不赋予 `/api/*` 权限。本阶段不实现协议建议中的机器 GET 状态路由。
2. 所有机器请求禁止 query、fragment、chunked 与重复/模糊认证头，必须给出唯一十进制 `Content-Length`。manifest 上限保持精确 1 MiB；artifact 的长度、SHA-256、MIME 和标识必须来自当前 manifest 的 missing map，不能借路由上传额外文件。签名继续绑定原始规范 path、method、node、key、timestamp、nonce 与正文 SHA-256，成功验证正文后才持久认领 nonce。
3. artifact 先流式写入 `/var/lib/instant-ai/blogger-agent/staging`，同时计算长度与压缩正文 SHA-256；staging 与正式 `artifacts` 目录启动时必须验证位于同一文件系统。媒体进一步检查 MP4 `ftyp`、JPEG、PNG 或 WebP 真实签名；评论包进一步流式 gzip 解压并核对未压缩长度、SHA-256、UTF-8 NDJSON 对象与条数。失败或断流一律清理临时文件。
4. 正式文件名只由接收端根据 transfer、artifact 类型和预期 SHA-256 生成，绝不拼接来源 filename。文件先 `fsync`，再以 `os.replace` 原子提交并同步目录元数据；已有目标只有在普通文件、长度和 SHA-256 全部相同时才可作为幂等结果复用，不同内容返回冲突且不得覆盖。
5. 独立 SQLite 账本升级到 schema 3，新增 `artifacts` 与 `processing_queue`，并为 transfer 保存稳定 opaque work key、`transport_status` 和完成时间。schema 2 会就地增加字段并从已保存 manifest 回填 pending artifact，不读取外部真实业务数据。较新 revision 继续取代当前版本，旧 transfer 不再接受 artifact 或 complete。
6. complete 缺任一 artifact 或正式文件时固定返回 409，且不创建处理任务。齐全后以 transfer_id 唯一插入一条 `processing_status=awaiting_asr_approval`，幂等 complete 不重复排队；回执严格返回 `status=completed`、`transport_completed=true`、`artifacts_verified=true`、`intelligence_status=awaiting_asr_approval` 及北京端要求的 transfer/receipt 身份。
7. complete 不调用 ffmpeg、豆包、普通 ASR、模型、`queue_analysis` 或目录监听器。未来只有主人明确批准后，独立消费者才可把 `awaiting_asr_approval` 推进到后续状态；传输接口本身永远不能自动产生费用。
8. 生产对等配置只从 Git 外 `/etc/instant-ai/blogger-transfer.env` 注入单一 node/key 与至少 32 字节十六进制 HMAC 密钥。systemd 文件缺失时主人服务仍启动、机器路由返回 503；Caddy 只复用既有 HTTPS 终止和回环反代，不开放 18765，不保存或记录密钥。
9. 评论 artifact 的未压缩 NDJSON 固定为北京 `blogger-comments/v1` 的 21 个公开业务字段。接收端逐行拒绝未知/重复字段、NaN/Infinity、账号 ID、`raw_json`、路径字段、错误类型/枚举/时区、重复评论 ID 和非稳定排序；评论作者账号标识不属于跨云协议。
10. v1 媒体矩阵固定为 `video → video/mp4`，以及 `image|cover → image/jpeg|image/png|image/webp`。不支持 audio、`application/mp4`、`application/octet-stream` 或 role/MIME 错配；非法项必须在 manifest 阶段拒绝，不能等 artifact 上传后形成不可完成传输。
11. complete 返回 `artifacts_verified=true` 前，必须从账本固定相对路径经无链接父链和安全普通文件句柄重新读取每个 artifact，核对长度、文件身份稳定性和完整 SHA-256；不得只相信先前 verified 状态或文件长度。
12. manifest 和 complete 必须在读取正文前先验证声明摘要绑定的 HMAC、时间窗与机器身份，读取后再核对实际摘要。共享 HTTP 服务设置 30 秒空闲读取超时和 32 个并发请求硬上限，超限明确返回 503，防止未认证慢请求占满主人站点线程。

## 数据与权限边界

- 运行数据根仍固定为 `/var/lib/instant-ai/blogger-agent`；数据库、staging、artifact、评论正文和处理队列不进入 Git。
- artifact 与处理队列不写即时财经 `instant_ai.db`，不写 `/var/lib/instant-ai/model-mr`，不读取北京 Cookie、浏览器资料、本地绝对路径或真实数据库。
- manifest 中的展示 filename 只留在隔离账本供审计，永远不是文件系统路径；正式相对路径由服务端生成。
- 当前主人查询层可在后续阶段使用 opaque work key、artifact expected/verified 与显式 processing status；客户端不得从 transport 状态猜测 `awaiting_asr_approval`。

## 验证与回滚

- 自动测试覆盖机器/主人权限互斥、manifest 1 MiB 基线、七行签名与先验认证、禁 query/chunked、精确长度、断流清理、冻结 MIME、媒体真实签名、评论 21 字段/gzip/未压缩 hash/长度/条数、同长度篡改与父链链接、并发/超时上限、幂等、冲突不覆盖、缺附件 409、唯一处理任务及 complete 零 ASR/AI 调用。
- 回滚代码不会删除 Git 外账本或 artifact。若机器对等配置异常，可移走 Git 外环境文件并重启，主人站点继续工作而机器路由明确不可用；不得用放宽主人认证、公开目录或手工改数据库绕过。
