---
name: crash-feature-extract
description: 从 dmesg、kern 或 vmcore-dmesg 提取内核崩溃特征（bug 类型、RIP、调用栈、模块、指纹）。没有 panic、oops 或 Call Trace 时返回 matched_crash=false。不检索、不写入任何知识库。
whenToUse: 日志里出现 kernel panic、oops、Call Trace、BUG、soft lockup、hard lockup 等内核崩溃特征时
---

# 内核崩溃特征提取

只做本地解析。不要查询已知问题库、社区案例，也不要写入知识库。上游 diff 交给 `gitcode`。

## 文本日志

```bash
python3 extract.py --file <工作区中的日志路径>
```

在本技能目录下执行。stdout 是一份 JSON：

- `matched_crash`: 是否识别为内核崩溃
- `signature`: `bug_type::模块::函数::偏移`，未识别时为空字符串
- `crash_features`: bug_type、bug_key、rip、rip_function、call_trace_text、related_modules
- `host_features`: 能从日志里读到的主机名和内核版本

`matched_crash` 为 false 时停止本技能，把诊断交回普通日志流程。

## vmcore

仅当本机已有 `crash` 和匹配的 vmlinux：

```bash
python3 extract.py --vmcore <vmcore> --vmlinux <vmlinux>
```

没有 vmlinux 时不要下载符号，改解析同目录的 `vmcore-dmesg.txt`。
