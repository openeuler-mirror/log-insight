"""pytest 根配置.

1. 将 backend/ 加入 sys.path, 使 `import app.*` 可用;
2. 在任何 app 模块被导入前, 注入测试用的真实基础设施连接
   (真实 PostgreSQL + Redis; 环境变量优先, 便于 CI 覆盖)。

测试规范 (见项目全局记忆): 测试必须跑在真实 infra 上, 禁止内存假实现替换 DB/Redis。
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 真实 infra: 本地 PostgreSQL(库 loginsight) 与 Redis(测试专用 db9)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://loginsight:loginsight@127.0.0.1:5432/loginsight")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/9")
os.environ.setdefault("SEED_DEMO_DATA", "False")  # 测试不触发演示数据种子