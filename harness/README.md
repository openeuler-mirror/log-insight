# LogInsight Harness

智能诊断页的智能体由 **DeepSeek Harness（dsh）** 驱动，本目录是随仓库维护的 harness 配置，部署后自动生效，无需在服务器上手工安装技能。

## 目录

| 路径 | 作用 |
| --- | --- |
| `AGENTS.md` | 智能体的项目指令：工作区边界、诊断原则、输出格式 |
| `skills/` | 技能（DSH 的 `SKILL.md` 目录），由 DSH 自动发现并按需加载 |
| `profile/` | 可选的 `dsh web` 配置档案，供本地或独立调试使用 |

当前技能：

- `log-diagnosis`：日志诊断主流程（取证 → 关联 → 结论）
- `linux-triage`：Linux 主机常见故障的排查清单

## 接入方式

后端通过 ACP（Agent Client Protocol）在独立子进程中启动 `dsh-acp`，每个诊断会话一个工作区：

```text
data/diagnosis/<会话ID>/
├── .dsh -> <仓库>/harness          # 技能目录（只读）
├── AGENTS.md -> <仓库>/harness/AGENTS.md
├── .sessions/                       # DSH 会话日志
└── uploads/<任务ID>/...             # 用户选中的日志（软链接）
```

相关环境变量：

| 变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 平台默认 DeepSeek 密钥（可选，未配置时页面会提示） |
| `AGENT_MODEL` | DeepSeek 默认模型，默认 `deepseek-flash` |
| `AGENT_HARNESS_DIR` | 本目录路径，容器内为 `/srv/app/harness` |
| `AGENT_PERMISSION_MODE` | 默认 `workspace-write`，限制写入工作区 |

## 模型与供应商

DSH 有两条模型通道，平台都支持：

| 通道 | 供应商 | 配置方式 |
| --- | --- | --- |
| `deepseek-official` | DeepSeek 官方 / 协议兼容网关 | 环境变量 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_PROTOCOL`，模型目录用 `DEEPSEEK_MODELS_JSON` |
| pi-ai 路由 | OpenAI / Anthropic / Google / xAI / Groq / Mistral / Moonshot / Z.AI / OpenRouter / 自定义网关 | `$DSH_HOME/settings.yaml` 的 `llm-pi-ai.providers`，ACP 入口用 `DSH_PROVIDER` 选择路由 |

每个诊断会话有独立的 `$DSH_HOME`，后端在启动 ACP 前写入：

```yaml
llm-pi-ai:
  providers:
    openai:
      apiKeyEnv: DSH_LLM_API_KEY
      displayName: OpenAI
      models:
        - id: gpt-4.1
          name: GPT-4.1
```

模型 id 与显示名称以供应商 `/models` 接口返回为准；读取不到时前端可手动填写。
连接凭据按供应商独立保存，模型选择与推理强度是独立配置：换模型不需要重新连接供应商。
推理强度由 DSH 目录能力决定，选中模型后通过 ACP 的 `session/set_config_option`
（`configId: reasoning_effort`）在会话上应用；模型不支持时不提供该选项。
多供应商路由由 `deploy/agent/loginsight-acp.mjs` 提供（容器内命令 `dsh-acp-loginsight`），
DeepSeek 通道仍使用官方 `dsh-acp`。

## 独立调试（可选）

```bash
cp -r harness/profile "$DSH_HOME/profiles/loginsight"
DEEPSEEK_API_KEY=sk-... dsh --profile loginsight web
```

改动 `skills/` 或 `AGENTS.md` 后无需重启，下一次请求即生效。
