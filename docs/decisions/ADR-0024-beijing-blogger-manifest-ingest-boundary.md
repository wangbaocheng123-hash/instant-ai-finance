# ADR-0024：北京采集中心到新加坡博主资料域的签名清单边界

- 状态：`ACCEPTED`
- 日期：2026-08-30
- 决策者：产品所有者明确要求把博主智能体拆为北京采集端与新加坡处理端，并开始逐步实施

## 背景

> 2026-08-31 补充：北京服务器上的既有模型下载器不再作为本决定的迁移或替换对象。两套服务并存，博主采集服务只复用服务器资源和可复用协议思想，不改写、清理或接管原模型下载器。新加坡实现直接进入即时 AI 现有仓库；详见 ADR-0026。

所有者决定不把整套博主智能体部署在一台服务器：北京服务器与模型下载器共用采集能力，负责下载作品、抓取评论、整理作者回复/点赞和基础分类；新加坡即时 AI 服务器保存对应博主资料，并在后续阶段承担豆包转写、标题编辑、内容解析和主人手机阅读。北京只需主动推送，新加坡之外的 GPT 不需要访问北京服务器。

即时 AI 已有模型先生单主人资料库，但它的目录、接口、数据契约和界面均以模型先生为固定对象。财经新闻数据库又受 72 小时、5 天和 7 天生命周期约束。把新博主资料直接写入任一现有数据域都会破坏隔离边界。

## 决定

1. 新加坡新增独立 `blogger-agent` 运行数据域，生产根目录固定为 `/var/lib/instant-ai/blogger-agent`。第一阶段只创建独立 SQLite 清单账本 `database/blogger_ingest.db`，不得写入即时财经新闻 `instant_ai.db` 或 `/var/lib/instant-ai/model-mr`。
2. 第一阶段只实现离线可测的北京权威 `blogger-transfer/v1` JSON 清单契约、HMAC-SHA256 验签、五分钟时间窗、持久 nonce 防重放、传输幂等与作品修订乱序保护；不增加公网 HTTP 路由，不连接北京服务器，不接收媒体正文或评论正文。字段语义与规范化算法以北京端 `mx_agent/transfer_contract.py` 和 `docs/transfer-protocol-v1.md` 为准。
3. 清单正文上限为 1 MiB。顶层只允许 `schema_version`、`captured_at`、`collector`、`creator`、`work`、`media`、`comment_snapshot`、`revision_sha256` 与 `transfer_id`；所有嵌套对象也使用精确白名单。`creator.creator_id` 必须是 UUID，`work.revision` 必须是大于零的整数。额外或重复 JSON 字段、非 UTF-8、非有限数字、路径型文件名、重复媒体 ID 和未知主协议版本一律拒绝。平台和来源 URL 在本阶段按北京权威契约作为有界字符串接收，不另造北京端不存在的域名规则。
4. `media` 必须是数组，最多 100 项；每项只保存 `media_id`、角色、跨 Windows/Linux 安全的展示文件名、MIME、字节数、SHA-256 和顺序号，并按 `ordinal + role + media_id` 规范排序。`comment_snapshot` 只保存完整度统计与 `blogger-comments/v1+ndjson`、`gzip` bundle 描述符，且 `captured_count` 必须等于 bundle 的 `item_count`。本阶段不读取上述 artifact 正文。
5. 请求签名正文固定为七行 UTF-8：`METHOD`、`REQUEST_PATH`、`NODE_ID`、`KEY_ID`、`TIMESTAMP`、`NONCE`、`CONTENT_SHA256`。接收端先检查 1 MiB 上限、五分钟时间窗、正文 SHA-256 和 HMAC-SHA256，再解析清单并要求请求头 node/key 与 `collector.node_id/key_id` 一致，最后在独立 SQLite 中以规范化后的 `(node_id, nonce)` 原子认领 nonce。签名密钥由调用方注入内存，第一阶段不提供密钥文件读取器；密钥值不得进入 SQLite、Git、响应或日志。账本只保存非敏感 key ID、请求摘要和使用过的 nonce。
6. `revision_sha256` 是不含两个派生哈希字段的清洗、排序后规范清单 SHA-256。`transfer_id` 不是 UUID，而是对 `collector.node_id + creator.creator_id + work.platform + work.source_work_id + work.revision + revision_sha256` 六行身份正文计算的确定性 SHA-256；接收端必须独立重算并比对，不能信任发送值。
7. 每条作品以 `work.platform + creator.creator_id + work.source_work_id` 定位，以递增 `work.revision` 排序；`collector.source_sequence` 只用于审计，不能覆盖作品 revision。较老清单可审计保存为 `stale`，但绝不覆盖当前清单；较新清单原子取代当前版本，旧当前版本标记为内部 `superseded`；同一 revision 出现不同内容或同一 transfer ID 试图映射不同规范正文时拒绝。相同当前传输的新 nonce 重试返回 `duplicate`；已被更高修订替代的旧传输重试返回 `stale` ACK。
8. manifest 回执状态只对外使用 `accepted`、`duplicate`、`stale`，字段固定为安全的 `receipt_id`、`transfer_id`、`revision_sha256`、`work_revision`、`current_revision` 和稳定排序的 `missing_artifacts`。第一阶段首次接受或当前版本重复时，根据 manifest 元数据把 media ID 与原始 comment bundle ID 全列为 missing；stale 回执必须返回空数组。每个 missing 项只包含 `artifact_id` 与 `artifact_kind=media|comment_bundle`，不伪造上传完成状态。
9. 第一阶段不得创建媒体目录、下载视频、启动 ffmpeg、调用豆包或其他 AI、产生费用、修改标题或执行评论分析。媒体上传、评论分批、处理队列、主人 UI 和真实 HTTPS 接口分别作为后续阶段实施，并继续复用现有主人登录保护。
10. 博主资料不套用 ADR-0010 的财经新闻短周期清理。其正式生命周期尚未由所有者决定；在形成后续 ADR 前，第一阶段不实现自动删除、归档或长期保留承诺。
11. 时变罗盘、模型先生现有接口、即时财经采集和新闻数据库行为保持不变。本决定不授权读取或迁移任何现有业务数据库、媒体、Cookie、密钥或运行日志。

## 第一阶段数据状态

- `accepted`：当前已接受的最高修订。
- `stale`：乱序到达且低于当前修订，只保留审计记录。
- `superseded`：仅为账本内部状态，表示记录曾经是当前版本、随后被更高修订替代；旧传输再次请求时对外回 `stale`。
- `duplicate`：相同传输的幂等重试，不重复写入。

冲突、签名失败、正文摘要不符、请求过期和 nonce 重放都不会改变作品当前版本。

跨端兼容性由固定 golden vector 锁定：测试使用显式 32 字节测试密钥，以及由北京权威实现生成的固定正文、六个请求头、revision SHA-256、transfer ID 与 HMAC；新加坡接收器必须原样验收。该测试密钥仅是源码内公开测试数据，不是生产凭据。

## 后续闸门

1. 接入真实 HTTPS 前，必须为北京端建立 Git 外独立对等密钥、严格 JSON 请求上限、TLS 校验和脱敏运行状态；机器凭据只能写入清单和读取自身传输状态，不能读取新闻、模型先生或主人资料。
2. 接收评论正文前，必须冻结分批契约、字段白名单、作者互动标记和完整性摘要；不得携带 Cookie、头像、主页、粉丝资料、原始评论账号 ID、本机路径或原始 JSON。
3. 接收媒体前，必须采用流式临时文件、长度与 SHA-256 双校验和原子激活，不把视频一次性读入 Python 内存，也不由 Caddy 公开媒体目录。
4. 自动豆包转写属于可能付费动作。除非所有者另行批准自动计费策略、并发上限和失败重试规则，任务只能进入等待主人确认状态。

## 回滚

第一阶段没有接入生产服务或现有数据库。回滚只需停止使用新模块并删除尚未部署的 Git 外测试账本；不得删除或修改即时财经新闻、模型先生资料、时变罗盘数据或服务器其他目录。
