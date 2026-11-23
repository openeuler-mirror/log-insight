---
name: log-report
description: 把本次诊断写成一份日志报告。普通日志、内核崩溃和 vmcore 共用同一份 JSON，崩溃与 vmcore 只作为可选附录。模型只填 JSON，HTML 由脚本渲染。
whenToUse: 取证结束后需要输出可归档的诊断报告时
---

# 日志诊断报告

模型只写 `report.json`，不要手写 HTML。

```bash
python3 scripts/render_report.py report.json report.html
```

## 正文（始终填写）

1. `summary`：现象与范围（发生了什么、影响对象、时间范围）
2. `timeline`：关键日志。每条必须有 `file`、`line`、`text`，有时间再写 `time`
3. `analysis`：`status` 取 `confirmed`、`suspected`、`insufficient` 之一；`conclusion` 一到三句；`evidence` 每条含标题、原文摘录、推理
4. `actions`：处置步骤、如何验证、还缺什么材料

证据不足时 `status` 用 `insufficient`，不要编造根因。

多个异常只有在时间窗、故障域、影响面都对得上独立故障时才拆进 `faults`。只是同一故障的多个候选原因时，留在 `analysis.evidence` 里，不要拆成多个故障。时间上先后但因果未证实，写「相关」，不要写成「导致」。

## 附录（没有就省略或设为 null）

- `kernel_crash`：仅当 `crash-feature-extract` 返回 `matched_crash=true`。填入它的 `bug_type`、`rip`、`rip_function`、`call_trace_text`、`signature`。没有原始崩溃段时不要留空的宕机页，关键日志已在 `timeline`。
- `vmcore`：仅当跑过 `vmcore-analysis`。写入置信度（高 / 中 / 低 / 疑似硬件）和结论摘要。缺少 vmlinux 时在 `note` 写明缺符号。

检测结果来自 `witty-log-detection` 的工具返回。不要调用知识库写入或社区案例检索来填这份报告。没有真实工具时间线就不要写 `workflow`。

## 写作

- 摘录必须能在日志里对上文件和行号
- 证据链写「看到什么 → 因此判断什么」
- 关键数字要说明出处
- 置信度与 `status` 一致：单类证据用 `suspected`
