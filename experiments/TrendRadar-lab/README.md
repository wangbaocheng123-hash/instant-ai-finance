# TrendRadar 实验目录

状态：`RUNTIME_VERIFIED_WITH_DEFECTS`。

- 来源：官方固定提交 `8ee26026ba6c11dec41a95fb3895a7162876caa1` 的隔离副本。
- Python：项目局部 `.venv`，Python 3.14.2；101 个分发包，248.88 MiB；`pip check` 通过。
- Doctor：默认 GBK 因 emoji 失败；`python -X utf8 -m trendradar --doctor` 以 8 通过、2 警告、0 失败完成。
- 受控数据：单一 `cls-hot` 热榜 13 条、公开测试 RSS 20 条；第二轮均为 0 新增并更新已有记录。
- 产物：当日 news/RSS SQLite、26 条排名历史、两个 HTML 时间快照和 latest，新增输出约 6.89 MiB。
- 安全边界：AI、翻译、通知、S3、MCP 均关闭，无真实密钥。
- 缺陷：RSS-only 被热榜开关错误门控；RSS 新增检测与数据库幂等结果矛盾；严重一致性错误仍返回 exit 0；doctor 默认 GBK 不兼容 emoji。

后续测试性改动与依赖安装必须继续限定在本目录，不得修改 `upstream/` 原件。
