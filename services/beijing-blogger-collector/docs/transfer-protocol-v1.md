# 博主智能体跨云传输协议 v1

## 适用范围

本协议只负责把北京采集端取得的公开作品、媒体和评论可靠传给新加坡智能端。北京不运行豆包、GPT、知识库或公网 MCP；新加坡不保存北京的 Cookie、浏览器资料和本地绝对路径。

协议版本固定为 blogger-transfer/v1。接收端遇到未知主版本必须拒绝，不能猜测字段。

## 身份和顺序

- creator.creator_id：跨服务器预先登记的不可变 UUID。
- creator.platform_user_id：平台稳定用户 ID；抖音使用 sec_uid。显示名称不是主键。
- work.source_work_id：平台作品 ID；抖音使用 aweme_id。
- collector.source_sequence：同一北京节点全局单调递增，允许因崩溃产生空洞，绝不复用。
- work.revision：同一作品单调递增，允许空洞，绝不复用。
- revision_sha256：清洗、排序后的规范清单 SHA-256。
- transfer_id：节点、博主、作品、revision 和 revision_sha256 的确定性 SHA-256；同一版本重试时必须复用原始清单和 transfer_id。

接收端按作品 revision 决定：

- incoming > current：应用。
- incoming < current：作为 stale 确认，不覆盖现有数据。
- incoming == current 且哈希相同：幂等重复，返回原回执。
- incoming == current 且哈希不同：冲突，拒绝并人工检查。

source_sequence 用于发现节点事件空洞和审计，不能单独作为作品覆盖条件。

## 评论包

评论不内嵌普通 manifest。最多五万条评论先形成 UTF-8 NDJSON，按以下键稳定排序：

1. display_order
2. root_source_comment_id；缺失时使用 source_comment_id
3. parent_source_comment_id
4. source_comment_id

NDJSON 使用 gzip 压缩，mtime 固定为 0。manifest 的 comment_snapshot 只保存完整度和 bundle 描述：

    {
      "snapshot_id": "snapshot-...",
      "captured_at": "2026-08-30T10:05:00+08:00",
      "complete": false,
      "expected_total": 1200,
      "captured_count": 986,
      "top_level_count": 800,
      "reply_groups": 120,
      "reply_groups_incomplete": 4,
      "missing_replies": 8,
      "orphan_replies": 1,
      "rules_version": "comment-rules/v1",
      "bundle": {
        "bundle_id": "<compressed sha256>",
        "format": "blogger-comments/v1+ndjson",
        "content_encoding": "gzip",
        "item_count": 986,
        "size_bytes": 123456,
        "sha256": "<compressed sha256>",
        "uncompressed_size_bytes": 654321,
        "uncompressed_sha256": "<plain ndjson sha256>"
      }
    }

v1 评论项采用精确字段白名单，只允许：`source_comment_id`、`parent_source_comment_id`、`root_source_comment_id`、`reply_to_comment_id`、`author`、`is_creator`、`text`、`like_count`、`reply_count`、`published_at`、`captured_at`、`kind`、`section`、`sentiment`、`risk_level`、`author_liked`、`low_value`、`ip_label`、`public_label`、`actual_reply_user`、`display_order`。其中 `author_liked` 保留 true、false、null 三态。

评论作者的任何账号标识均不属于传输协议，`author_uid`、`sec_uid`、`user_id` 等字段必须移除；`raw_json`、本机路径、Cookie、调试字段及其他未知字段也不得进入 NDJSON。`complete=false` 时只能合并，不能删除接收端旧评论。v1 即使 `complete=true` 也不根据“本次未出现”自动删除历史评论。

生产发送端应流式生成 bundle 文件；内存构建函数只用于小批量与单元测试。

## 媒体

manifest 只保存展示文件名、角色、MIME、长度和 SHA-256。本地路径只允许保存在北京私有 outbox SQLite。

v1 的 role×MIME 白名单固定为：

| role | MIME |
| --- | --- |
| `video` | `video/mp4` |
| `image` | `image/jpeg`、`image/png`、`image/webp` |
| `cover` | `image/jpeg`、`image/png`、`image/webp` |

v1 不支持 `audio` role；`audio/mpeg`、`audio/mp4`、`application/mp4`、`application/octet-stream` 以及任何不匹配上述 role 的 MIME 不得进入可发送 manifest。北京 collector 必须在写入 outbox 前决定性省略并报告不支持数量，直接构造的非法 manifest 则由协议校验拒绝。

接收端必须：

1. 流式写入同文件系统临时目录。
2. 同时计算长度和 SHA-256。
3. 检查 MP4 或图片的真实格式。
4. 全部通过后原子移动到正式目录。
5. 使用服务端生成的规范文件名，不能把来源 filename 直接拼接为路径。
6. 明确排入豆包转写队列，不能依赖下载目录监听器偶然发现。

## 请求签名

每个 manifest、媒体分块、评论包和完成请求均使用 HTTPS 与 HMAC-SHA256。规范签名正文是七行 UTF-8：

    METHOD
    REQUEST_PATH
    NODE_ID
    KEY_ID
    TIMESTAMP
    NONCE
    CONTENT_SHA256

请求头：

- X-Blogger-Node-Id
- X-Blogger-Key-Id
- X-Blogger-Timestamp
- X-Blogger-Nonce
- X-Blogger-Content-SHA256
- X-Blogger-Signature

接收端先校验时间窗口、正文哈希和签名，再在持久化数据库中认领 nonce。同一节点和 nonce 在有效期内只能成功一次。密钥只保存在环境文件，日志只能记录 key_id、错误码和请求 ID。

## 建议接口

- POST /internal/v1/transfers：提交 manifest，返回回执和缺失 artifact。
- PUT /internal/v1/transfers/{transfer_id}/media/{media_id}：流式整文件上传媒体。
- PUT /internal/v1/transfers/{transfer_id}/comments/{bundle_id}：流式上传评论包。
- POST /internal/v1/transfers/{transfer_id}/complete：请求校验和入库。
- GET /internal/v1/transfers/{transfer_id}：北京主动查询回执。

北京只发起出站 HTTPS；不需要让 GPT 或公网主动访问北京。

v1 使用内容寻址的整文件幂等上传，不提供 Range、分块会话或断点续传。网络中断后从文件开头重传；接收端对相同 artifact ID、长度和 SHA-256 返回幂等回执。若实际单文件规模使重传成本不可接受，必须在后续新协议版本中增加可持久化上传会话，不能把本地 `uploaded_bytes` 误称为服务端断点。

## 北京 outbox

持久化状态：

    pending
      -> manifest_accepted
      -> media_uploading
      -> finalizing
      -> delivered

临时错误进入 retry_wait，并保留失败前状态；永久错误进入 dead_letter。网络超时、408、429 和 5xx 使用带随机抖动的指数退避。401、403、契约冲突和校验失败应暂停或进入人工检查，不能盲目重试。

收到新加坡 completed 回执以前不得标记 delivered，也不得删除本地媒体。v1 默认不自动删除北京原文件。

## 新加坡接收状态

建议独立保存：

    accepted
      -> media_pending
      -> validating
      -> importing
      -> queued_intelligence
      -> completed

豆包和 AI 状态另设 queued、transcribing、parsing、ready、skipped、failed，不能把“传输完成”等同于“智能分析成功”。
