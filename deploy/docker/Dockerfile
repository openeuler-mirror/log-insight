# 多阶段构建: 前端构建产物由 Nginx 容器托管, 后端双镜像 (api / worker)
# Node 24 仅用于运行 DeepSeek Harness ACP 子进程 (智能诊断)
FROM node:24-slim AS node

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /srv/app
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# Node 运行时 + DSH ACP 服务端
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g --registry=https://registry.npmmirror.com deepseek-harness-acp@0.1.31

# 多供应商 ACP 入口 (可选 pi-ai 路由): 放进包内以解析 @deepseek-ai/* 依赖
COPY deploy/agent/loginsight-acp.mjs /usr/local/lib/node_modules/deepseek-harness-acp/lib/loginsight-acp.mjs
RUN printf '#!/bin/sh\nexec node /usr/local/lib/node_modules/deepseek-harness-acp/lib/loginsight-acp.mjs "$@"\n' \
      > /usr/local/bin/dsh-acp-loginsight && chmod +x /usr/local/bin/dsh-acp-loginsight

COPY backend/ .
COPY harness/ /srv/app/harness/
ENV AGENT_HARNESS_DIR=/srv/app/harness

# ---- API 镜像入口 ----
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# ---- Worker 镜像入口 ----
FROM base AS worker
CMD ["python", "-m", "app.worker"]
