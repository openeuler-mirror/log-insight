# log-insight · 日志智能分析诊断平台

面向前端运维（O&M）人员的日志智能分析诊断平台，以 **规则引擎打底 + 按需 LLM 智能总结** 为核心，提供故障日志的并发上传与流式分析、智能巡检、巡检规则管理、性能离线分析以及团队协作与操作审计能力。

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
- **规则管理**：关键字 / 正则 / 阈值 / 脚本四类规则的增删改查；正则灾难性回溯防护、规则改动免重启即生效、变更审计
- **性能分析**：上传文件即解析的 ECharts 时序图表，支持 sar 系统性能（CPU / 内存 / 磁盘）/ blktrace 块设备 IO / netcap 网络抓包 / 中断计数变化（`/proc/interrupts` 多次快照或 `sar -I`）；解析在请求内完成，结果不落库仅本次展示
- **智能诊断**：三栏工作台（目录文件 / 文件阅读器 / 会话对话）；左栏直接浏览后端所在主机的真实磁盘日志（路径白名单校验），支持一次性嵌套加载、超大目录按需懒加载、勾选与拖拽文件加入提问上下文；目录变更由 watchdog 递归监听并经 SSE 推送，目录树与已打开文件内容实时刷新
- **个人知识库**：用户私有笔记 CRUD 与关键词 / 标签检索，严格 owner 隔离
- **审计日志**：团队级操作审计（任务 / 成员 / 规则变更），支持按动作与关键字检索

## 界面预览

| 登录 | 工作台总览 |
|------|------|
| ![登录页](docs/screenshots/01-login.png) | ![工作台总览](docs/screenshots/02-dashboard.png) |

| 团队列表 | 团队详情 |
|----------|----------|
| ![团队列表](docs/screenshots/03-teams.png) | ![团队详情](docs/screenshots/04-team-detail.png) |

| 创建解析任务 | 任务结果分析 |
|----------|----------|
| ![创建解析任务](docs/screenshots/05-task-wizard.png) | ![任务结果分析](docs/screenshots/06-task-result.png) |

| 规则管理 | 性能分析 · sar 性能分析 |
|----------|----------|
| ![规则管理](docs/screenshots/07-rules.png) | ![性能分析-sar性能分析](docs/screenshots/08-perf-sar.png) |

| 性能分析 · 网络抓包分析 | 性能分析 · blktrace 分析 |
|----------|----------|
| ![性能分析-网络抓包分析](docs/screenshots/09-perf-netcap.png) | ![性能分析-blktrace分析](docs/screenshots/10-perf-blktrace.png) |

| 性能分析 · 中断计数变化 | 智能巡检 |
|----------|----------|
| ![性能分析-中断计数变化](docs/screenshots/11-perf-interrupts.png) | ![智能巡检](docs/screenshots/12-inspection.png) |

| 巡检详情 | 智能诊断 |
|----------|----------|
| ![巡检详情](docs/screenshots/13-inspection-detail.png) | ![智能诊断](docs/screenshots/14-diagnosis.png) |

| 智能诊断 · 图片预览 | 智能诊断 · 侧栏折叠 |
|----------|----------|
| ![智能诊断-图片预览](docs/screenshots/15-diagnosis-image.png) | ![智能诊断-侧栏折叠](docs/screenshots/16-diagnosis-collapsed.png) |

| 智能诊断 · 运行状态折叠 | |
|----------|----------|
| ![智能诊断-运行状态折叠](docs/screenshots/17-diagnosis-status.png) | |

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

智能诊断的「目录文件」为**独立于上传分析链路**的只读通道，不经过 Redis 与 Worker：

```
浏览器 (Vue3 SPA, EventSource)
   │  GET /api/fs/{roots,list,tree,read,raw}   (?token= 认证)
   ▼
API 服务 ── 路径白名单校验(FS_ROOTS) ──► 后端所在主机文件系统
   ▲                                              │ watchdog 递归监听
   └──────── SSE /api/fs/watch ◄──────────────────┘
                 (created / modified / moved / deleted)
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · Alembic · APScheduler · watchdog（目录递归监听） |
| 中间件 | PostgreSQL 16（元数据 + 时间分区指标）· Redis 7（任务队列 / 事件总线 / 限流） |
| 前端 | Vue 3 · TypeScript · Pinia · Vue Router · ECharts · Vite 6（o-design 风格，无重型组件库） |
| 智能分析 | 规则引擎优先 + 按需 LLM（OpenAI 兼容 API，可选，未配置时自动降级为纯规则） |
| 实时推送 | SSE（`sse-starlette`，EventSource 认证经 `?token=` 查询参数）；同时用于分析进度 / LLM 流式 / 目录变更 |

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
npm run dev          # 开发模式，http://localhost:5180
npm run typecheck    # vue-tsc 类型检查
npm run build        # 生产构建，产物在 dist/
```

开发模式下接口请求（含 SSE）由 Vite 代理至 `http://localhost:8000`，且对 `text/event-stream` 关闭缓冲。

> 智能诊断左栏浏览的是**后端进程所在主机**的文件系统，因此本地开发时看到的是本机磁盘；生产环境如需限定可见范围，请配置 `FS_ROOTS`（见「配置说明」）。

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

## 使用说明

登录后左侧导航按页面权限渲染，主要页面：工作台 / 团队 / 规则管理 / 性能分析 / 智能巡检 / 智能诊断 / 个人知识库。

### 智能诊断工作台（三栏）

| 区域 | 操作 |
|------|------|
| 左 · 目录文件 | 点「目录」/「文件」按钮弹出服务端路径选择框，选定后加载该目录的嵌套树。<br>点击行：目录展开 / 文件在中间栏打开；复选框勾选（勾选目录等价于勾选其下全部文件，标题栏显示「N 已选」）；拖拽行到右栏输入区加入提问上下文。<br>超大目录（子项超过 `FS_EAGER_MAX_CHILDREN`）自动改为展开时懒加载。<br>**重新选择目录会清空上一次的勾选与展开状态**（含重选同一目录）。 |
| 左 · 运行状态 | 底端停靠面板，展示 Todo List / 模型信息 / 工具调用；点右上角箭头可折叠为一条底栏。 |
| 中 · 文件阅读器 | 多标签页；文本可切换查看 / 编辑，支持保存与还原（草稿未保存时磁盘变更不覆盖内容）；图片直接预览。 |
| 右 · 会话对话 | 会话页签（1/2/3…）可新建 / 关闭；页签栏右侧显示运行状态（运行中 / 空闲）并可清空当前会话；输入区可调「模式」与「模型」，Enter 发送、Shift+Enter 换行。 |

> 说明：智能诊断当前**仅「目录文件」已对接后端真实磁盘**（含勾选 / 拖拽 / 实时刷新）；右侧对话的消息、工具调用与运行状态为前端演示数据，尚未接入真实 agent / 大模型。

### 实时刷新

后端以 watchdog 递归监听所选目录，磁盘上新增 / 修改 / 重命名 / 删除文件后，经 SSE 推送到前端：目录树**定点刷新**受影响的已加载目录（保留展开状态）；已打开且无未保存编辑的文件自动重载最新内容，被删除时自动关闭对应页签，被重命名时同步更新页签名与路径。

### 三栏宽度

左右两条竖栏支持左右拖动调整宽度（主体栏自动吸收剩余宽度，主体内容随之自适应换行），单击竖栏可收起 / 展开对应侧栏；侧栏折叠与容器缩放时宽度会自动收敛，保证主体栏不被挤压。

## 项目结构

```
log-insight/
├── backend/
│   ├── app/
│   │   ├── api/            # auth / users / teams / notices / audit / dashboard /
│   │   │                   # tasks / analysis(聚合+LLM+SSE) / rules / metrics / perf /
│   │   │                   # rule_suggest / knowledge(个人知识库) / fs(本地文件浏览+watchdog 监听)
│   │   ├── core/           # 配置 / 安全(JWT) / 数据库 / 双级 RBAC 依赖注入 / 审计 / 表结构守卫
│   │   ├── models/         # 用户-角色-权限 / 团队-成员-通知 / 任务 / 解析事件 /
│   │   │                   # 规则 / 指标 / 用户偏好 / 审计 / 规则推荐
│   │   ├── modules/
│   │   │   ├── analyze/    # 6 解析器插件 / 聚合 / 五段报告 / 演示日志
│   │   │   ├── diagnosis/  # 诊断流水线 / 规则引擎 / 脚本执行器 / LLM 接入
│   │   │   ├── inspection/ # 巡检流水线 / 检查项插件 / 规则推荐
│   │   │   └── perf/       # 性能采集解析（sar / blktrace / netcap / 中断计数变化）
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
│       ├── api/fs.ts       # 本地文件浏览接口封装（tree/list/read/raw + EventSource watch url）
│       ├── stores/         # rbac / teams / tasks / audit
│       ├── views/          # 登录 / 工作台 / 团队×2 / 任务向导 / 解析结果 / 规则 / 性能 /
│       │                   # 巡检×2 / 智能诊断 / 个人知识库
│       ├── components/     # ChartBase / 团队面板 / 性能分析面板（sar / netcap / blktrace / 中断计数变化）/
│       │                   # DiagnosisFileTree(目录文件树+勾选拖拽) / DiagnosisFileReader(多页签阅读器) /
│       │                   # DiagnosisPathPicker(服务端路径选择) / DiagnosisInspector(运行状态)
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
| `FS_ROOTS` | 智能诊断允许浏览的根目录（逗号分隔绝对路径）；留空则默认用户主目录 + Windows 各盘符 |
| `FS_EAGER_MAX_CHILDREN` | 单目录子项超过该值时该目录改为懒加载，避免一次性拉取过多（默认 300） |
| `FS_TREE_MAX_DEPTH` / `FS_TREE_MAX_NODES` | 一次性嵌套加载的最大深度 / 节点总数（默认 10 / 10000） |
| `FS_LIST_MAX` | 单层列表返回条数上限（默认 2000） |
| `FS_MAX_TEXT_MB` | 文本文件读取上限（MB），超出仅提示不加载（默认 50） |

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
- [ ] 智能诊断对话接入真实 agent / 大模型（当前仅「目录文件」对接后端真实磁盘，对话与运行状态为前端演示数据）
- [ ] 巡检模板与定时巡检任务管理界面
- [ ] 用户管理界面（目前为种子数据 admin + 演示账号）
- [ ] 诊断报告证据链（findings 关联原始日志行号）
- [ ] TimescaleDB 平滑升级路径（Linux）

## License

- 后端及整体项目：MIT
- 前端（`frontend/`）：基于 [witty-log-analyzer](https://atomgit.com/StackTony/witty-log-analyzer) 修改，遵循 [MulanPSL-2.0](frontend/LICENSE-witty)