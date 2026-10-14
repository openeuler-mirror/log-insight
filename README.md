# log-insight · 日志智能分析诊断平台

面向前端运维（O&M）人员的日志智能分析诊断平台，以 **规则引擎打底 + 按需 LLM 智能总结** 为核心，提供故障日志的并发上传与流式分析、智能巡检、巡检规则管理、性能数据图表以及团队协作与操作审计能力。

- **部署形态**：ARM（鲲鹏）/ x86 双架构，Linux 与 Windows 均支持（裸机 systemd / NSSM 与 Docker Compose 两套方案）
- **访问方式**：Web 端（浏览器）
- **技术栈**：Python（FastAPI）+ PostgreSQL + Redis；前端 Vue 3 + TypeScript
- **规模定位**：约 1k 用户、并发上传 100+ QPS，API 与分析 Worker 均为无状态、可横向扩展
- **日志来源**：被动上传模式（用户在目标服务器收集日志包后上传，平台不主动采集）

## 功能特性

- **用户权限控制**：JWT + 平台级 RBAC，叠加团队级三角色（创建者 / 管理员 / 普通成员）；令牌刷新与黑名单登出
- **团队协作**：团队创建 / 申请加入 / 邀请审批 / 成员角色调整 / 通知中心；按团队隔离的任务与规则作用域
- **日志解析**：6 种解析器插件（Linux syslog / Nginx / Windows 事件 / Redfish / Java 应用 / 通用兜底），上传前 512KB 切片预检，支持文件上传与粘贴两种模式
- **故障诊断日志分析**：日志包入队即返回，Redis Stream 队列并发分析，SSE 实时流式输出，原始日志服务端分页筛选
- **智能巡检**：规则引擎优先匹配 + 按需 LLM 智能总结（SSE 流式生成，未配置 LLM 自动降级为纯规则）
- **智能巡检规则推荐**：对已完成巡检任务评估规则覆盖度并推荐可新增规则项，用户可编辑并一键接纳
- **规则管理**：关键字 / 正则 / 阈值 / 脚本四类规则的增删改查；正则灾难性回溯防护、版本化热加载、变更审计
- **性能分析**：上传文件即解析的 ECharts 时序图表，支持 sar 系统性能（CPU / 内存 / 磁盘）/ blktrace 块设备 IO / netcap 网络抓包；解析在请求内完成，结果不落库仅本次展示
- **审计日志**：团队级操作审计（任务 / 成员 / 规则变更），支持按动作与关键字检索

## 界面预览

| 登录 | 工作台总览 |
|------|------|
| ![登录页](docs/screenshots/01-login.png) | ![工作台总览](docs/screenshots/02-dashboard.png) |

| 团队列表 | 团队详情 |
|----------|----------|
| ![团队列表](docs/screenshots/03-teams.png) | ![团队详情](docs/screenshots/04-team-detail.png) |

| 任务结果分析 | 规则管理 |
|----------|----------|
| ![任务结果分析](docs/screenshots/05-task-result.png) | ![规则管理](docs/screenshots/06-rules.png) |

| 性能分析 · sar 性能分析 | 性能分析 · 网络抓包分析 |
|----------|----------|
| ![性能分析-sar性能分析](docs/screenshots/07-perf-charts.png) | ![性能分析-网络抓包分析](docs/screenshots/08-perf-netcap.png) |

| 性能分析 · blktrace 分析 | 智能巡检 |
|----------|----------|
| ![性能分析-blktrace分析](docs/screenshots/09-perf-blktrace.png) | ![智能巡检](docs/screenshots/10-inspection.png) |

## 整体架构

```
浏览器 (Vue3 SPA, SSE 客户端)
   │  REST 上传/CRUD/分页查询        │  SSE 进度/LLM 流式
   ▼                                ▼
Nginx (反代, 上传缓冲, SSE 不缓冲)
   ▼
API 服务 ×N (FastAPI, 无状态) ──► Redis (Stream 任务队列 / Pub-Sub 事件 / 限流黑名单)
   │                                     ▲
   │ 任务入队 (Redis Stream)              │ 事件回推
   ▼                                     │
分析 Worker ×N (独立进程) ───────────────┘
   解压校验 → 解析器插件 → 事件入库 → 聚合统计 → 规则引擎 → 按需 LLM 报告
   ▼
PostgreSQL (元数据 + 时间分区指标) + 本地存储 (日志包)
```

**核心数据流**：被动上传 → 入队即返回 `task_id` → Worker 并发异步分析 → 进度经 Redis 事件通道 → SSE 流式回推浏览器；LLM 报告与规则推荐按需触发（用户点击才调用），同样走 SSE。详细设计见 [docs/框架设计.md](docs/框架设计.md)。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · Alembic · APScheduler |
| 中间件 | PostgreSQL 16（元数据 + 时间分区指标）· Redis 7（任务队列 / 事件总线 / 限流） |
| 前端 | Vue 3 · TypeScript · Pinia · Vue Router · ECharts · Vite 6（o-design 风格，无重型组件库） |
| 智能分析 | 规则引擎优先 + 按需 LLM（OpenAI 兼容 API，可选，未配置时自动降级为纯规则） |
| 实时推送 | SSE（`sse-starlette`，EventSource 认证经 `?token=` 查询参数） |

## 快速开始

### 1. 环境准备

- Python 3.11+、Node.js 18+
- PostgreSQL 15+、Redis 7+（本机服务或容器均可）

### 2. 后端

```bash
cd backend

# 安装依赖（建议先创建虚拟环境）
pip install -r requirements.txt

# 配置环境
copy .env.example .env      # Windows
# cp .env.example .env      # Linux，并按需修改数据库 / Redis / LLM 连接信息

# 启动 API 服务（首次启动自动建表并初始化 admin 账号）
uvicorn app.main:app --reload --port 8000

# 另开终端，启动分析 Worker
python -m app.worker
```

默认管理员账号：`admin / admin123`（首次登录后请修改）。API 文档：http://localhost:8000/docs

演示账号（密码统一 `demo123`，`SEED_DEMO_DATA=True` 时自动初始化，生产环境请置 `False` 关闭）：

| 账号 | 姓名 | 平台角色 | 团队角色（演示团队） |
|------|------|----------|----------------------|
| guchuang | 顾创建 | 管理员 | 北京A · 创建者 |
| jiangguanli | 蒋管理 | 普通用户 | 北京A · 管理员；上海B · 创建者 |
| sunputong | 孙普通 | 普通用户 | 北京A · 普通成员（有一条待处理邀请） |
| zhangshenpi | 张审批 | 普通用户 | 上海B · 管理员（有一条待审批申请） |
| liyiban | 李一般 | 普通用户 | 无团队（可体验申请加入流程） |

登录页提供演示账号一键填充，便于快速体验不同角色视角下的权限差异。

### 3. 前端

```bash
cd frontend
npm install
npm run dev          # 开发模式，http://localhost:5173
npm run build        # 生产构建，产物在 dist/
```

开发模式下接口请求由 Vite 代理至 `http://localhost:8000`。

### 4. Docker Compose 一键部署

```bash
cd deploy/docker
docker compose up -d       # 拉起 postgres / redis / api / worker / web
```

镜像支持 `linux/amd64` 与 `linux/arm64` 双架构：`docker buildx build --platform linux/amd64,linux/arm64 ...`；可通过 `--scale api=N`、`--scale worker=N` 横向扩展。

### 5. 裸机部署

- **Linux（systemd）**：`deploy/linux/install.sh`
- **Windows（NSSM 服务）**：`deploy/windows/install-nssm.ps1`
- **Nginx 反代**：`deploy/nginx/nginx.conf`（含 SSE 专用配置 `proxy_buffering off`）

## 项目结构

```
log-insight/
├── backend/
│   ├── app/
│   │   ├── api/            # auth / users / teams / notices / audit / dashboard /
│   │   │                   # tasks / analysis(聚合+LLM+SSE) / rules / metrics / perf / rule_suggest
│   │   ├── core/           # 配置 / 安全(JWT) / 数据库 / 双级 RBAC 依赖注入 / 审计 / 表结构守卫
│   │   ├── models/         # 用户-角色-权限 / 团队-成员-通知 / 任务 / 解析事件 /
│   │   │                   # 规则 / 指标 / 用户偏好 / 审计 / 规则推荐
│   │   ├── modules/
│   │   │   ├── analyze/    # 6 解析器插件 / 聚合 / 五段报告 / 演示日志
│   │   │   ├── diagnosis/  # 诊断流水线 / 规则引擎 / 脚本执行器 / LLM 接入
│   │   │   ├── inspection/ # 巡检流水线 / 检查项插件 / 规则推荐
│   │   │   └── perf/       # 性能采集解析（sar / blktrace / netcap）
│   │   ├── queue/          # Redis Stream 任务队列
│   │   ├── events/         # 事件总线（Pub/Sub + Stream 留存）
│   │   ├── worker/         # Worker 消费循环 + 定时调度
│   │   ├── metrics/        # 指标分区管理 / 查询 / 降采样
│   │   └── main.py         # 应用入口 + 幂等种子（权限/角色/admin/演示数据）
│   ├── alembic/            # 数据库迁移（版本脚本 + env.py）
│   ├── rules_scripts/      # 脚本规则目录（约定 check(ctx) 的脚本）
│   ├── tests/              # 后端测试（按功能点分目录，真实 PostgreSQL + Redis 运行）
│   ├── conftest.py         # 测试基础设施
│   ├── requirements.txt    # 运行依赖
│   └── requirements-dev.txt
├── frontend/               # Vue3 + TS + Pinia（o-design 风格，基于 witty-log-analyzer）
│   └── src/
│       ├── api/http.ts     # axios 封装：Bearer + 驼峰化 + 401 刷新重放
│       ├── stores/         # rbac / teams / tasks / audit
│       ├── views/          # 登录 / 工作台 / 团队×2 / 任务向导 / 解析结果 / 规则 / 性能 / 巡检×2
│       ├── components/     # ChartBase / 团队面板 / 性能分析面板（sar / netcap / blktrace）
│       ├── rbac/           # 权限常量（与后端权限点镜像）
│       └── parser/         # 解析器元数据 + 聚合类型 + 演示日志生成器
├── deploy/                 # docker-compose / Dockerfile / Nginx / 裸机脚本
└── docs/                   # 框架设计 / 功能点清单 / 脚本规则设计 / 规则表设计 / 架构图 / 界面截图
```

## 配置说明

后端配置由 `backend/.env` 提供（见 [backend/.env.example](backend/.env.example)），主要项：

| 变量 | 说明 |
|------|------|
| `SECRET_KEY` | JWT 签名密钥，生产环境务必替换为随机串 |
| `DATABASE_URL` / `REDIS_URL` | PostgreSQL / Redis 连接串 |
| `SEED_DEMO_DATA` | 是否初始化演示账号与示例数据，生产环境置 `False` |
| `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容 LLM 接入，留空则降级为纯规则分析 |
| `WORKER_CONCURRENCY` | 单 Worker 并发分析任务数 |
| `RULE_SCRIPT_DIR` / `RULE_SCRIPT_TIMEOUT` | 脚本规则目录与子进程超时限制 |

## 开发约定

- **提交粒度**：每次提交的变动行数不超过 **1000 行**；大改动拆分为多个小提交，便于回溯与审查
- **提交信息**：使用中文，格式 `type(scope): 摘要`（`feat` / `fix` / `docs` / `refactor` / `style` / `test` / `chore`）
- **测试目录**：后端测试按功能点分目录置于 `backend/tests/<功能点>/`（如 `auth_rbac` / `log_diagnosis` / `log_inspection`），用例以真实 PostgreSQL + Redis 运行；暂无用例的功能点用 `.gitkeep` 占位

## 文档

- [框架设计](docs/框架设计.md)：总体架构、模块设计、并发扩展模型与设计决策记录
- [功能点清单](docs/功能点清单.md)：客户确认的功能点与实现进展
- [脚本规则设计](docs/脚本规则设计.md) / [规则表设计](docs/规则表设计.md)：规则引擎与脚本规则约定

## 后续规划

- [ ] 具体服务器类型的日志解析器插件（Drain3 模板化解析）
- [ ] 巡检模板与定时巡检任务管理界面
- [ ] 用户管理界面（目前为种子数据 admin + 演示账号）
- [ ] 诊断报告证据链（findings 关联原始日志行号）
- [ ] TimescaleDB 平滑升级路径（Linux）

## License

- 后端及整体项目：MIT
- 前端（`frontend/`）：基于 [witty-log-analyzer](https://atomgit.com/StackTony/witty-log-analyzer) 修改，遵循 [MulanPSL-2.0](frontend/LICENSE-witty)