# 脚本规则目录 (`rules_scripts/`)

放置「脚本规则」(`match_type = script`) 可执行的 Python 脚本。当某个诊断任务命中一条
脚本规则时, Worker 会以**独立子进程**执行对应脚本 (见 [`docs/脚本规则设计.md`](../../docs/脚本规则设计.md))。

## 目录约定

- 本目录下 `*.py` 即候选脚本, 规则表只存**相对路径** (如 `oom_burst.py`)。
- `_` 开头的文件/目录视为内部模块, 不在 `GET /api/rules/scripts` 下拉中暴露。
- 路径经接口层严格校验: 相对路径 + `.py` 后缀 + 解析后必须位于本目录内 (禁止 `..`/绝对路径/逃逸)。
- 脚本内容改动**下次任务即生效** (每次执行重新读盘), 不随规则 `version` 联动。

## 接口契约

脚本必须暴露 `check(ctx)` 入口, 返回 `{"findings": [...], "logs": [...]}`。

### 输入 `ctx`

```json
{
  "rule":  {"id": 12, "name": "OOM 突发检测", "severity": "critical", "params": {"threshold": 3}},
  "task":  {"id": "3f2b...", "task_type": "diagnosis", "server_type": "linux"},
  "entries": [
    {"line_no": 1, "raw": "...", "ts": "2026-10-10T12:00:00+08:00",
     "level": "error", "component": "kernel", "file": "syslog", "fields": {}}
  ],
  "metrics": [{"host": "server-bj-01", "metric_name": "cpu.usage", "value": 91.2, "metric_time": "...", "tags": {}}],
  "file_stats": {"syslog": {"lines": 12000, "errors": 30, "warns": 12}},
  "meta": {"total_entries": 12000, "total_metrics": 576, "truncated": false}
}
```

- `entries[].ts` 可能为 `null` (行内无时间戳); `line_no` 为**全任务全局递增**, `file` 为来源文件名。
- 上下文有上限: 条数 `SCRIPT_MAX_ENTRIES` (默认 50000)、字节 `SCRIPT_MAX_BYTES` (默认 16MB);
  超出时截断并置 `meta.truncated = true`, 脚本应据此降级 (如仅做抽样统计)。
- 脚本参数通过 `ctx["rule"]["params"]` 读取 (来源于规则表的 `script_params`)。

### 输出

```json
{
  "findings": [
    {"line_no": 42, "matched_text": "Out of memory", "raw": "...",
     "message": "60 秒内第 4 次 OOM", "severity": "critical"}
  ],
  "logs": ["窗口统计: 4 次"]
}
```

| 键 | 必填 | 说明 |
|----|------|------|
| `findings` | 是 | 命中列表, 可为空数组 |
| `findings[].line_no` | 否 | 关联日志行号; 0 或省略表示与具体行无关 (如指标类) |
| `findings[].message` | 否 | 自定义说明, 优先于 `raw` 展示 |
| `findings[].severity` | 否 | 覆盖规则级别 (仅 `info/warn/error/critical`), 非法则回退规则默认值 |
| `logs` | 否 | 调试信息, 推送任务事件流, 不写入报告正文 |

- 输出条数上限 `SCRIPT_MAX_FINDINGS` (默认 200), 超出截断。
- 脚本异常/超时**不使任务失败**: 该规则记入报告 `content.script_errors` 并在事件流发 `warning`。

## 内置示例

| 脚本 | 用途 | 建议 `script_params` |
|------|------|----------------------|
| `oom_burst.py` | 窗口内 OOM 重复次数超阈值 | `{"window_seconds": 60, "threshold": 3, "keyword": "Out of memory"}` |
| `clock_skew.py` | 同文件内时间戳回退检测 | `{"min_back_seconds": 5, "max_report": 20}` |

## 编写要求

- 脚本以 Worker 相同系统用户身份运行, 等价于「受信人员可在服务器上执行代码」——只允许 `rule:manage` 权限者配置。
- 仅依赖标准库或 backend 环境已安装的第三方库 (无独立依赖声明)。
- 保持幂等、无副作用; 单次执行应远快于规则超时 (默认 5s)。