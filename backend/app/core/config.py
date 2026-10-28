"""应用配置 (pydantic-settings, 环境变量优先, .env 兜底)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 基础
    APP_NAME: str = "log-insight"
    DEBUG: bool = False
    API_PREFIX: str = "/api"

    # 安全
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # 数据库 / 缓存
    DATABASE_URL: str = "postgresql+asyncpg://loginsight:loginsight@localhost:5432/loginsight"
    REDIS_URL: str = "redis://localhost:6379/0"
    # 连接池预算 (API/Worker 进程各自独立计数; 总和须小于 PG max_connections)
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10
    AUTH_CACHE_TTL_SECONDS: int = 30   # 用户角色/权限缓存, 0 表示禁用

    # 存储
    DATA_DIR: str = "./data"
    MAX_UPLOAD_SIZE_MB: int = 2048

    # Redis Stream 任务队列
    QUEUE_STREAM: str = "loginsight:tasks"
    QUEUE_GROUP: str = "workers"
    QUEUE_MAX_LEN: int = 100000

    # 事件通道 (SSE 断线补发)
    EVENT_STREAM_PREFIX: str = "loginsight:events"
    EVENT_CHANNEL_PREFIX: str = "loginsight:ch"
    EVENT_TTL_SECONDS: int = 3600

    # Worker
    WORKER_CONCURRENCY: int = 8          # 单 Worker 同时分析任务数
    WORKER_CONSUMER_NAME: str = ""       # 留空自动生成 hostname-pid, 多副本必须保持唯一
    WORKER_PENDING_TIMEOUT_MS: int = 600000  # 超过该时长的 pending 任务视为崩溃遗留, 重新投递
    WORKER_RECLAIM_INTERVAL_SECONDS: int = 60  # 周期性回收超时 pending 消息的间隔
    WORKER_RETRY_MAX: int = 2            # 处理异常时的重试次数, 超过后标记任务失败
    WORKER_SHUTDOWN_TIMEOUT_SECONDS: int = 60  # 优雅停机时等待在途任务的上限

    # LLM (OpenAI 兼容 API, 留空则降级为纯规则)
    LLM_API_BASE: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_MAX_CONCURRENCY: int = 4

    # 智能诊断智能体 (DeepSeek Harness, 通过 ACP 驱动)
    AGENT_ENABLED: bool = True
    AGENT_ACP_CMD: str = "dsh-acp"                 # ACP 服务端 (deepseek-official 路由)
    AGENT_ACP_MULTI_CMD: str = "dsh-acp-loginsight"  # 可选供应商的 ACP 入口 (pi-ai 路由)
    AGENT_HARNESS_DIR: str = "./harness"           # 仓库内 skills / AGENTS.md
    AGENT_MODEL: str = "deepseek-flash"
    AGENT_API_KEY: str = ""                        # 留空时回退 DEEPSEEK_API_KEY 环境变量
    AGENT_API_BASE: str = ""
    AGENT_PROTOCOL: str = "messages"               # messages / chat-completions
    AGENT_PERMISSION_MODE: str = "workspace-write" # workspace-write / danger-full-access
    AGENT_AUTO_APPROVE: bool = True                # 自动放行工作区内工具调用
    AGENT_TIMEOUT_SECONDS: int = 600               # 单轮诊断超时
    AGENT_STREAM_MAX_BYTES: int = 4 * 1024 * 1024  # 单文件预览上限

    # 指标
    METRIC_RETENTION_DAYS: int = 90

    # 脚本规则 (docs/脚本规则设计.md): 脚本目录 + 子进程执行限制
    RULE_SCRIPT_DIR: str = "./rules_scripts"   # 相对 backend 运行目录
    RULE_SCRIPT_TIMEOUT: int = 5               # 全局默认超时 (秒), 规则级 script_timeout=0 时使用
    RULE_SCRIPT_CONCURRENCY: int = 4           # 单 Worker 并发脚本子进程数
    SCRIPT_MAX_ENTRIES: int = 50000            # 传给脚本的日志条目上限 (超出截断)
    SCRIPT_MAX_BYTES: int = 16 * 1024 * 1024   # 传给脚本的上下文 JSON 序列化上限 (16MB)
    SCRIPT_MAX_FINDINGS: int = 200             # 脚本单次输出 findings 上限

    # 上传限流 (按用户, Redis 计数)
    UPLOAD_RATE_LIMIT_PER_MIN: int = 30

    # 演示数据种子 (5 个演示账号 demo123 + 示例团队/任务); 生产环境置 False
    SEED_DEMO_DATA: bool = True

    # 本地文件浏览 (智能诊断左栏「目录文件」)
    FS_ROOTS: str = ""                 # 允许浏览的根目录(逗号分隔绝对路径); 空则默认用户主目录 + Windows 盘符
    FS_EAGER_MAX_CHILDREN: int = 300   # 单目录子项超过该值 → 该目录改懒加载, 避免一次性拉取过多
    FS_TREE_MAX_DEPTH: int = 10         # 一次性嵌套加载的最大深度
    FS_TREE_MAX_NODES: int = 10000     # 一次性嵌套加载的节点总数上限
    FS_LIST_MAX: int = 2000            # 单层列表返回条数上限(懒加载)
    FS_MAX_TEXT_MB: float = 50          # 文本文件读取上限(MB), 超出只提示不加载

    # 团队知识库 (docs/知识库设计.md)
    KB_ENABLED: bool = True
    KB_DEFAULT_TOP_K: int = 10
    KB_DEFAULT_QUERY_COUNT: int = 8
    KB_SEARCH_RECALL_PER_QUERY: int = 30
    KB_CONTEXT_MAX_CHARS: int = 24000
    KB_CHUNK_SIZE: int = 512
    KB_CHUNK_OVERLAP: int = 64
    KB_MAX_UPLOAD_MB: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
