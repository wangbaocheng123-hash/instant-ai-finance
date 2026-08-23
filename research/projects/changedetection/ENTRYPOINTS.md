# changedetection.io 入口清单

## 主启动与 CLI

| 类型 | 源码路径 | 符号/配置 | 说明 | 状态 |
|---|---|---|---|---|
| 直接脚本 | `changedetection.py` | `if __name__ == '__main__'` | 调用 `changedetectionio.main()` | `SOURCE_VERIFIED` |
| Python 包主入口 | `changedetectionio/__init__.py` | `main` | CLI 解析、store 初始化、app 构造、server/batch 启动 | `SOURCE_VERIFIED` |
| console script | `setup.py` | `entry_points.console_scripts` | 安装后命令 `changedetection.io=changedetectionio:main` | `SOURCE_VERIFIED` |
| CLI 参数 | `changedetectionio/__init__.py` | `print_help`、`getopt` | `-h/-p/-d/-l/-C/-P/-u/-uN/-r/-b/-s` | `SOURCE_VERIFIED` |

`-u` 可添加 watch，`-r` 可入队指定/all watch，`-b` 处理队列后退出。batch 模式仍会构造 app/worker，但跳过 HTTP server、ticker、通知 runner 和版本检查；因此其适合作为一次性检查入口，但本子任务未运行验证。

## Web 与 UI 入口

| 入口 | 源码路径 | 符号 | 说明 |
|---|---|---|---|
| Flask app | `changedetectionio/flask_app.py` | 全局 `app = Flask(...)` | 静态目录、模板目录、CORS、CSRF、登录、Babel |
| app 工厂式装配 | `changedetectionio/flask_app.py` | `changedetection_app` | 注入 datastore、注册 API/blueprint、启动 worker/线程 |
| watchlist/UI | `changedetectionio/blueprint/ui/__init__.py`、`blueprint/watchlist/__init__.py` | `construct_blueprint` | 编辑、预览、diff、queue 和 watch overview |
| RSS | `changedetectionio/blueprint/rss/blueprint.py` | `construct_blueprint` | 注册主 feed、单 watch、tag feed |
| 实时 | `changedetectionio/realtime/socket_server.py` | `init_socketio` | Socket.IO 状态推送 |

默认监听由 `LISTEN_HOST`（默认 `0.0.0.0`）和 `PORT`（默认 `5000`）决定；CLI `-h/-p` 可覆盖。源码入口见 `changedetectionio/__init__.py::main`。

## REST API 入口

`changedetectionio/flask_app.py::changedetection_app` 使用 Flask-RESTful 注册：

- `/api/v1/watch`
- `/api/v1/watch/<uuid>`
- `/api/v1/watch/<uuid>/history`
- `/api/v1/watch/<uuid>/history/<timestamp>`
- `/api/v1/watch/<uuid>/difference/<from>/<to>`
- `/api/v1/watch/<uuid>/favicon`
- `/api/v1/tags`
- `/api/v1/tag[/<uuid>]`
- `/api/v1/search`
- `/api/v1/notifications`
- `/api/v1/import`
- `/api/v1/systeminfo`
- `/api/v1/full-spec`

API 资源来自 `changedetectionio/api/*.py`；请求 schema 来自 `docs/api-spec.yaml` 并由 `changedetectionio/api/__init__.py::validate_openapi_request` 验证；`changedetectionio/api/auth.py::check_token` 检查 `x-api-key`，但全局设置允许禁用 API token。

## 定时任务入口

| 入口 | 源码路径 | 类/函数 | 触发方式 |
|---|---|---|---|
| 周期调度 | `changedetectionio/flask_app.py` | `ticker_thread_check_time_launch_checks` | app 初始化时创建 `TickerThread-ScheduleChecker` |
| worker pool | `changedetectionio/worker_pool.py` | `start_workers` → `start_async_workers` | `changedetection_app` 根据 `FETCH_WORKERS`/设置启动 |
| 单 watch 处理 | `changedetectionio/worker.py` | `async_update_worker` | 从 `RecheckPriorityQueue` 获取任务 |
| 通知处理 | `changedetectionio/flask_app.py` | `notification_runner` | 默认一个或 `NOTIFICATION_WORKERS` 个线程 |
| 版本检查 | `changedetectionio/flask_app.py` | `check_for_new_version` | 未禁用时每天连接 changedetection.io |

项目未使用独立 cron 定义作为常规调度器；定时检查是 Flask 进程内 ticker。CLI batch 提供外部 cron/任务计划程序可调用的入口，但本结论仅来自源码/帮助文案，未运行。

## Docker 入口

| 文件 | 入口/配置 | 结论 |
|---|---|---|
| `Dockerfile` | `ENTRYPOINT ["/docker-entrypoint.sh"]` | 先处理额外 Python 包 |
| `Dockerfile` | `CMD ["python", "./changedetection.py", "-d", "/datastore"]` | 正式 app 入口，容器数据位于 `/datastore` |
| `docker-entrypoint.sh` | `EXTRA_PACKAGES`、`exec "$@"` | 每次启动按需 pip 安装插件后 exec 主命令 |
| `docker-compose.yml` | `ghcr.io/dgtlmoon/changedetection.io`、`127.0.0.1:5000:5000` | 默认只映射 loopback，named volume 保存 datastore |

Docker 当前环境未安装，且本子任务无权安装或运行。

## 关键配置入口

- 包/依赖：`setup.py`、`requirements.txt`、`runtime.txt`
- 容器：`Dockerfile`、`docker-compose.yml`、`docker-entrypoint.sh`
- 全局默认：`changedetectionio/model/App.py::model.base_config`
- watch 默认：`changedetectionio/model/__init__.py::watch_base.__init__`
- LLM schema：`changedetectionio/model/LLMSettings.py::LLMSettings`
- OpenAPI：`docs/api-spec.yaml`
- 数据目录 CLI：`changedetectionio/__init__.py::main` 的 `-d`
- 环境变量：分散在入口、fetcher、worker、validator、LLM 和 notification 模块，详见 `CONFIGURATION.md`。

## 证据约束

本文件全部结论对应项目 `dgtlmoon/changedetection.io`、提交 `fce24780e74199bf34c62a0d90188cc2fc12f061`，验证状态均为 `SOURCE_VERIFIED`；启动可用性、端口占用和命令实际结果均为 `UNVERIFIED`。

