# 北京“北极采集器”快捷图标交接包

本目录是北京统一采集入口的 Git 交接检查点，不是新加坡即时 AI 的运行依赖。它只包含公开品牌图片、Web App Manifest 和共享 HTML `<head>` 片段，不包含视频、评论、数据库、Cookie、密码、密钥或生产配置。

## 已准备内容

- `static/north-pole-collector-icon-1024.png`：保留的高清图标母版。
- `static/north-pole-collector-icon-512.png`、`static/north-pole-collector-icon-192.png`：PWA 图标。
- `static/apple-touch-icon.png`：iPhone/iPad 添加到主屏幕使用的 180×180 图标。
- `static/favicon-32.png`：浏览器页签图标。
- `static/manifest.webmanifest`：名称为“北极采集器”的 PWA 清单。
- `head-snippet.html`：需要合入北京采集器共享页面模板的标签。
- `verify-assets.sh`：只读核验图像尺寸、PNG 像素格式、Manifest 和 HTML 引用。

## 生产现状与接入边界

2026-09-05 的公网只读核对显示：`https://collector.amuyeye.com/health` 返回 `ok`，但根登录页的 `<head>` 尚无 manifest、Apple touch icon 或 favicon 引用，`/manifest.webmanifest` 返回 404。因此本目录已经完成图标设计和 Git 交接，尚未宣称北京生产已换图标。

北京端接入时必须从已经上线的采集器 `1.0.5` / 提交 `1b4e018` 或其安全后继提交开始：

1. 先确认北京采集器真实源码仓库与生产目录，不根据本文猜测路径，也不修改原模型下载器仓库或运行数据。
2. 将 `static/` 中六个文件复制到采集器现有静态目录；不要把即时 AI 仓库变成北京服务运行时依赖。
3. 把 `head-snippet.html` 的七行标签合入北京服务的共享 `<head>`，确保根页、登录页、模型下载器页和博主采集页都继承同一图标。
4. 若实际框架没有 `/static/` 路由，应在北京采集器源码中建立等价的只读静态路由，并同步调整 manifest 与标签路径；不得用 Caddy 直接暴露源码目录或 Git 外业务目录。
5. 运行北京采集器完整回归和本目录 `./verify-assets.sh`。图标发布不应启动采集、修改 outbox、重传媒体、调用 ASR/豆包/AI，或改动 `model-downloader.service`。
6. 获得所有者针对本轮的明确正式发布口令后，再按北京采集器既有发布/回滚流程上线。发布后核验登录页 HTML、manifest、180/192/512 PNG、`/health` 与两套服务状态。

iPhone 已经添加过旧快捷方式时，Safari 通常不会立即替换其本地图标；生产上线并验收后，应删除旧快捷方式，再从 Safari“添加到主屏幕”重新添加一次。
