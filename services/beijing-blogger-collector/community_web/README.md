# 榫卯 AI 社区静态展示面

第一版只展示阿木爷爷抖音公开合集“榫卯108道”的标题和封面，点击卡片跳转原视频。页面不托管视频、不读取采集器或模型下载器资料，也不包含账号和写入能力。

## 本地预览

在本目录启动任意静态 HTTP 服务后打开首页，例如：

```powershell
python -m http.server 4173 --bind 127.0.0.1
```

正式服务由北京采集器在`/community/*`提供；独立域名的Caddy站点把根路径改写到该前缀。

## 更新作品

只从公开合集`https://www.douyin.com/collection/7288595551828854799/1`核对条目，并更新`videos.json`。每条必须保留连续集数、原始标题、精确`douyin.com/video/<ID>`和HTTPS`douyinpic.com`封面。不得混入推荐视频，不得把视频或封面二进制提交到Git。

更新后至少运行：

```powershell
python -m unittest tests.test_sunmao_community tests.test_beijing_deployment tests.test_collector_server
node --check community_web/app.js
```
