# changedetection.io Windows 运行手册（静态计划，未执行）

## 能否直接运行

源码具备 Windows 分支：`changedetectionio/__init__.py::main` 在 `os.name == 'nt'` 时默认使用 `%APPDATA%\changedetection.io` 并创建目录；`setup.py` 要求 Python `>=3.10`；requirements 包含 Windows timezone 补充 `tzdata`。因此基础 `html_requests` 模式理论上可直接运行，不要求 WSL 或 Docker。

但是，本子任务未安装依赖、未运行；结论是 `SOURCE_VERIFIED + RUNTIME_UNVERIFIED`。

## 运行时与依赖

- Python：`setup.py` 为 `>=3.10`；`runtime.txt` 写 `python-3.11.5`；Docker build 默认 Python 3.11。
- 安装：README-pip 文档给出 `pip3 install changedetection.io`；从源码通常为创建虚拟环境后安装 requirements/package，但本轮不执行。
- Windows caveat：`requirements.txt` 明示 `jq` 不在 Windows 自动安装；JSONPath 仍可用，jq selector 需另行批准/方案。
- JS 页面：源码通常通过 `PLAYWRIGHT_DRIVER_URL` 或 `WEBDRIVER_URL` 连接外部 browser 服务；仅基础 HTTP 无需 browser service。

## 最小候选命令（未执行）

以下仅来自源码入口与文档，不代表已经验证：

```powershell
# 安装后 console script；-C 在目录不存在时创建
changedetection.io -d 'H:\即时AI文件库\<未来批准的changedetection子目录>' -C -h 127.0.0.1 -p 5000

# 或源码入口
python .\changedetection.py -d 'H:\即时AI文件库\<未来批准的changedetection子目录>' -C -h 127.0.0.1 -p 5000
```

不得在 R0 直接采用上述 H 盘路径：实际子目录、数据边界和服务部署必须由主任务批准；本轮没有写入 H 盘。

## 建议的受控验证步骤

1. 用户批准依赖安装量和虚拟环境位置；
2. 使用隔离实验目录 `experiments/changedetection-lab`，不修改 upstream；
3. 创建项目局部 venv，不安装全局 Python 包；
4. 先禁用 browser/LLM，只使用 `html_requests` 和 loopback `127.0.0.1`；
5. 创建临时 datastore，不使用正式业务库；
6. 验证 UI、创建 watch、抓取、保存、diff、API、通知 null target；
7. 记录依赖下载量、venv/datastore 磁盘、日志和停止清理；
8. 若需 Docker/WSL/browser service，先停止并请求用户批准。

## 端口与配置

- 默认端口：5000；默认 host：`0.0.0.0`，Windows 本地验证应覆盖为 `127.0.0.1`。
- API：`/api/v1`，默认 API token enabled，header `x-api-key`。
- RSS：`/rss?token=<rss_access_token>`。
- datastore：`-d`；Windows 默认 `%APPDATA%\changedetection.io`。
- worker：`FETCH_WORKERS`，模型默认 requests workers 为 5；最小测试建议 1，但需运行阶段决定。
- 版本 telemetry：可用 `DISABLE_VERSION_CHECK=true` 关闭。

## 浏览器能力

- Playwright：`PLAYWRIGHT_DRIVER_URL=ws://...`，源码 `content_fetchers/playwright.py::fetcher.run` 连接 CDP。
- Selenium：`WEBDRIVER_URL=http://.../wd/hub`。
- Docker compose 给出外部 browser 服务示例，但本机 Docker 当前未安装，未经批准不得安装。

## 可能的 Windows 错误

| 风险 | 源码/静态依据 | 处理原则 |
|---|---|---|
| jq selector 不可用 | `requirements.txt` 明示 Windows 不自动安装 jq | 先使用 JSONPath；需要 jq 时单独评估 |
| browser endpoint 连接失败 | browser fetcher 默认外部 URL | 先基础 HTTP；browser service 另行批准 |
| 端口 5000 被占用 | batch/normal 入口均依赖端口 | 改 `-p`；不杀无关进程 |
| 路径权限/磁盘 | store 立即写 JSON/历史 | 用实验目录，记录空间和错误 |
| 多进程共用 datastore | `save_json_atomic` 文档明确不支持 | 一个 datastore 只运行一个实例 |
| gevent Windows 连接限制 | `requirements.txt` 注释 | 默认 threading，不先启 gevent |
| PDF 转换缺工具 | Docker 安装 `poppler-utils`，Windows未自动保证 | PDF 测试前单独评估 Poppler，不擅自安装 |

## 停止与清理（待实际验证）

- 前台运行：`Ctrl+C`，入口注册 SIGINT/SIGTERM 并关闭 worker/queue。
- 不使用强制删除 upstream 或正式 datastore。
- 实验产物清理只能针对解析后的 `experiments/changedetection-lab` 明确路径，并在主任务批准后执行。

## Docker 备选（未执行）

`Dockerfile` 暴露 5000，默认命令 `python ./changedetection.py -d /datastore`；`docker-compose.yml` 把服务映射到 `127.0.0.1:5000` 并使用 named volume。当前环境没有 Docker，且安装属于需用户批准的大型组件。

项目/提交：`dgtlmoon/changedetection.io` / `fce24780e74199bf34c62a0d90188cc2fc12f061`。

