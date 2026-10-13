# 榫卯 AI 社区

社区首页由三个底部入口组成：左侧“尺寸型”、中间“交流区”、右侧“榫卯108道”。默认主屏是交流区；尺寸型先保留后续工艺品尺寸连接的位置；榫卯108道继续展示阿木爷爷抖音公开合集的标题与封面，并跳转原视频。

## 交流区边界

- 游客填写称呼后即可发表纯文字，称呼最多20字，正文最多500字。
- 浏览器每15秒自动刷新一次公开内容。
- 服务端要求同源提交，并按来源地址限制为至少15秒一条、每小时最多10条；来源地址仅在内存中用于限流，不写入数据库。
- 社区内容单独保存在`community.sqlite3`，默认保留90天且最多5000条，不与采集器业务库混用。
- 当前不支持图片、附件、账号、私信或富文本；前端只以`textContent`显示内容。

## 本地预览

交流区需要后端接口，不能只用静态文件服务器。PowerShell 中可使用临时数据库启动完整本地预览：

```powershell
$env:BLOGGER_AGENT_ROLE="collector"
$env:BLOGGER_AGENT_COMMUNITY_DATABASE_PATH="$env:TEMP\sunmao-community-preview.sqlite3"
python -m mx_agent.collector_server --host 127.0.0.1 --port 4173
```

打开`http://127.0.0.1:4173/community/`。正式服务由北京采集器在`/community/*`提供；独立域名的Caddy站点把根路径改写到该前缀。

## 更新作品

只从公开合集`https://www.douyin.com/collection/7288595551828854799/1`核对条目，并更新`videos.json`。每条必须保留连续集数、原始标题、精确`douyin.com/video/<ID>`和HTTPS`douyinpic.com`封面。不得混入推荐视频，不得把视频或封面二进制提交到Git。

更新后至少运行：

```powershell
python -m unittest tests.test_sunmao_community tests.test_community_messages tests.test_beijing_deployment tests.test_collector_server
node --check community_web/app.js
```
