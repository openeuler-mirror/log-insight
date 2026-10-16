"""中断计数变化分析: 支持 /proc/interrupts 多次快照 与 sar -I (sysstat) 文本输出.

两种输入自动识别:

1. **/proc/interrupts 快照** (可拼接多次采样, 形如 `cat /proc/interrupts >> irq.txt`):
        CPU0   CPU1   CPU2   CPU3
   0:     12      0      0      0   IO-APIC   2-edge    timer
   LOC: 1234567 1234567 1234567 1234567   Local timer interrupts
   多快照时逐区间计算各中断源 / 各 CPU 的计数差值(变化量); 单快照仅给出当前累计计数。

2. **sar -I 输出** (`sar -I SUM -P ALL 1 10` 或 `sar -I ALL 1 10`):
   10:12:01 AM   CPU   INTR   intr/s
   10:12:11 AM   all   LOC    3456.78
   解析为中断速率时序(单位: 中断/秒)。

统一输出结构: 汇总(总量/均值/峰值) + per-CPU + Top 中断源 + 变化时序 + 采样点明细 + 告警。
"""
import re
from collections import OrderedDict

from app.modules.perf.errors import PerfAnalysisError

TOP_N = 15

# /proc/interrupts: 表头行由若干 CPU0 CPU1 … 组成; 数据行为 "标签: 计数… [描述]"
PROC_CPU_HEADER_RE = re.compile(r"^\s*(?:CPU\d+\s*)+$")
PROC_IRQ_RE = re.compile(r"^\s*([^:]+):\s*(.*)$")
# sar: 数据行以时钟开头(可带 AM/PM), 或为 Average 汇总行
SAR_TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2}:\d{2})\s*([AP]M)?\s+(.*)$")
SAR_AVG_RE = re.compile(r"^\s*[Aa]verage:\s*(.*)$")


class InterruptError(PerfAnalysisError):
    """输入内容无法识别为 /proc/interrupts 快照或 sar -I 输出."""


def _lead_ints(s: str) -> tuple[list[int], str]:
    """取行首连续整数列, 返回 (计数列表, 其余描述).

    描述里可能含数字(如 `IO-APIC 2-edge`), 因此只消费首个非纯数字 token 之前的列。
    """
    toks = s.split()
    nums: list[int] = []
    i = 0
    for t in toks:
        if t.isdigit():
            nums.append(int(t))
            i += 1
        else:
            break
    return nums, " ".join(toks[i:]).strip()


def _clock_seconds(clock: str) -> int:
    h, m, s = (int(x) for x in clock.split(":"))
    return h * 3600 + m * 60 + s


# ---------------------------------------------------------------- /proc/interrupts
def _parse_proc(text: str) -> dict:
    """解析 /proc/interrupts 多次快照, 计算相邻快照的中断计数变化量."""
    snapshots: list[dict[str, dict]] = []
    cpu_count = 0
    cur: dict[str, dict] | None = None

    for line in text.splitlines():
        if PROC_CPU_HEADER_RE.match(line):
            cpu_count = max(cpu_count, len(line.split()))
            cur = {}
            snapshots.append(cur)
            continue
        m = PROC_IRQ_RE.match(line)
        if not m or cur is None:
            continue
        label, rest = m.group(1).strip(), m.group(2)
        nums, desc = _lead_ints(rest)
        if nums:
            cur[label] = {"desc": desc, "counts": nums}

    if not snapshots:
        raise InterruptError("未解析到 /proc/interrupts 快照 (缺少 `CPU0 CPU1 …` 表头行)")
    if not any(snapshots):
        raise InterruptError("未解析到 /proc/interrupts 中断行 (形如 `LOC: 1 2 3 4 Local timer interrupts`)")

    labels: "OrderedDict[str, str]" = OrderedDict()
    for s in snapshots:
        for k, v in s.items():
            labels[k] = v["desc"]
    cpu_count = max(cpu_count, max((len(v["counts"]) for s in snapshots for v in s.values()), default=0))

    per_label_delta: dict[str, list[float]] = {k: [] for k in labels}
    per_label_total: dict[str, float] = {k: 0.0 for k in labels}
    per_cpu_delta = [0.0] * cpu_count
    interval_totals: list[float] = []
    sample_totals: list[float] = []
    resets = 0

    prev: dict[str, dict] | None = None
    for snap in snapshots:
        sample_totals.append(float(sum(sum(v["counts"]) for v in snap.values())))
        if prev is not None:
            iv_total = 0.0
            for k, v in snap.items():
                cur_counts = v["counts"]
                p = prev.get(k)
                if p is None:
                    deltas = list(cur_counts)  # 新增中断源: 本次计数全部计入
                else:
                    deltas = []
                    for i, cv in enumerate(cur_counts):
                        pv = p["counts"][i] if i < len(p["counts"]) else 0
                        dv = cv - pv
                        if dv < 0:
                            dv = 0  # 计数器回退(重置 / IRQ 重分配)
                            resets += 1
                        deltas.append(dv)
                dt = float(sum(deltas))
                per_label_delta[k].append(dt)
                per_label_total[k] += dt
                iv_total += dt
                if len(deltas) == cpu_count:
                    for i, dv in enumerate(deltas):
                        per_cpu_delta[i] += dv
            for k in labels:  # 本区间消失的中断源补 0, 保证各序列等长
                if k not in snap:
                    per_label_delta[k].append(0.0)
            interval_totals.append(iv_total)
        prev = snap

    last = snapshots[-1]
    per_cpu_last = [0] * cpu_count
    for v in last.values():
        if len(v["counts"]) == cpu_count:
            for i, cv in enumerate(v["counts"]):
                per_cpu_last[i] += cv

    intervals = len(snapshots) - 1
    if intervals == 0:  # 单快照: 无差值可算, 直接以当前累计计数作为展示值
        for k, v in last.items():
            per_label_total[k] = float(sum(v["counts"]))
        per_cpu_delta = [float(x) for x in per_cpu_last]

    total = float(sum(per_label_total.values()))
    avg = total / intervals if intervals else 0.0
    if interval_totals:
        pidx = max(range(len(interval_totals)), key=lambda i: interval_totals[i])
        peak = {"value": round(interval_totals[pidx], 2), "at": f"第 {pidx + 1} 个变化区间"}
    else:
        peak = {"value": 0.0, "at": ""}

    ranked = sorted(labels, key=lambda k: per_label_total[k], reverse=True)
    per_cpu = [
        {
            "cpu": f"CPU{i}",
            "value": round(per_cpu_delta[i], 2),
            "share": round(per_cpu_delta[i] / total * 100, 2) if total else 0.0,
            "last_total": per_cpu_last[i],
        }
        for i in range(cpu_count)
    ]

    def _top(limit: int) -> list[dict]:
        return [
            {
                "name": k,
                "desc": labels[k],
                "value": round(per_label_total[k], 2),
                "share": round(per_label_total[k] / total * 100, 2) if total else 0.0,
                "cpu_count": len(snapshots[-1].get(k, {}).get("counts", [])),
                "series": [round(v, 2) for v in per_label_delta[k]],
            }
            for k in ranked[:limit]
        ]

    items = [{"name": t["name"], "data": t["series"]} for t in _top(TOP_N)]
    if len(ranked) > TOP_N:
        others = [0.0] * intervals
        for k in ranked[TOP_N:]:
            for i, v in enumerate(per_label_delta[k]):
                others[i] += v
        items.append({"name": "其他", "data": [round(v, 2) for v in others]})

    warnings: list[str] = []
    if intervals == 0:
        warnings.append("仅检测到 1 个快照, 无法计算计数变化, 以下为当前累计计数")
    if resets:
        warnings.append(f"检测到 {resets} 处计数回退(计数器重置或 IRQ 重分配), 已按 0 计入")
    missing = [k for k in labels if any(k not in s for s in snapshots)]
    if missing:
        warnings.append(f"{len(missing)} 个中断源未在所有快照中出现, 缺失区间按 0 计")

    return {
        "format": "proc-interrupts",
        "unit": "count",
        "metric_label": "变化量",
        "cpu_count": cpu_count,
        "sample_count": len(snapshots),
        "interval_count": intervals,
        "interval_s": None,
        "total": round(total, 2),
        "avg": round(avg, 2),
        "peak": peak,
        "per_cpu": per_cpu,
        "top": _top(TOP_N),
        "series": {"x": [f"#{i + 1}" for i in range(intervals)], "items": items},
        "samples": [{"label": f"快照 {i + 1}", "total": round(t, 2)} for i, t in enumerate(sample_totals)],
        "warnings": warnings,
    }


# ---------------------------------------------------------------- sar -I
def _sar_cols(rest: str) -> tuple[str, str, float] | None:
    """解析 sar 数据行列: `<CPU> [INTR] rate`.

    `-I SUM` 为 2 列(CPU intr/s), `-I ALL` 为 3 列(CPU INTR intr/s)。
    """
    toks = rest.split()
    if len(toks) >= 3:
        cpu, name, raw = toks[0], toks[1], toks[-1]
    elif len(toks) == 2:
        cpu, name, raw = toks[0], "SUM", toks[1]
    else:
        return None
    try:
        return cpu, name, float(raw)
    except ValueError:
        return None


def _parse_sar(text: str) -> dict:
    """解析 sar -I 输出, 汇总为中断速率(中断/秒)时序."""
    points: "OrderedDict[str, dict[str, float]]" = OrderedDict()   # 时间点 -> {中断名: 速率}
    cpu_points: "OrderedDict[str, dict[str, float]]" = OrderedDict()  # 时间点 -> {CPU序号: 速率}
    names: set[str] = set()
    cpu_count = 0
    has_all = False

    for raw in text.splitlines():
        m = SAR_TIME_RE.match(raw)
        if m:
            clock, rest = m.group(1), m.group(3)
        else:
            m2 = SAR_AVG_RE.match(raw)
            if not m2:
                continue
            clock, rest = "Average:", m2.group(1)
        parsed = _sar_cols(rest)
        if parsed is None:
            continue
        cpu, name, value = parsed
        if clock == "Average:":
            continue
        if cpu == "all":
            has_all = True
            points.setdefault(clock, {})[name] = value
            names.add(name)
        elif cpu.isdigit():
            cpu_count = max(cpu_count, int(cpu) + 1)
            slot = cpu_points.setdefault(clock, {})
            slot[cpu] = slot.get(cpu, 0.0) + value

    if not points and cpu_points:  # 无 all 汇总行时, 退化为各 CPU 求和
        for clock, cpus in cpu_points.items():
            points[clock] = {"SUM": round(sum(cpus.values()), 2)}
        names = {"SUM"}
    if not points:
        raise InterruptError("未解析到 sar -I 中断数据行 (需含 `intr/s` 表头的采样行)")

    clocks = list(points.keys())
    name_series = {n: [points[c].get(n, 0.0) for c in clocks] for n in names}
    totals = [sum(points[c].values()) for c in clocks]
    total = float(sum(totals))
    avg = total / len(clocks) if clocks else 0.0
    pidx = max(range(len(totals)), key=lambda i: totals[i])
    peak = {"value": round(totals[pidx], 2), "at": clocks[pidx]}

    interval_s = None
    if len(clocks) >= 2:
        try:
            d = _clock_seconds(clocks[1]) - _clock_seconds(clocks[0])
            interval_s = float(d) if d > 0 else None
        except ValueError:
            interval_s = None

    ranked = sorted(names, key=lambda n: sum(name_series[n]), reverse=True)
    per_cpu = []
    for i in range(cpu_count):
        vals = [cpu_points.get(c, {}).get(str(i), 0.0) for c in clocks]
        per_cpu.append({
            "cpu": f"CPU{i}",
            "value": round(sum(vals) / len(vals), 2) if vals else 0.0,
            "share": 0.0,
            "last_total": round(vals[-1], 2) if vals else 0.0,
        })
    cpu_sum = sum(x["value"] for x in per_cpu)
    for x in per_cpu:
        x["share"] = round(x["value"] / cpu_sum * 100, 2) if cpu_sum else 0.0

    def _top(limit: int) -> list[dict]:
        out = []
        for n in ranked[:limit]:
            mean = sum(name_series[n]) / len(clocks)
            out.append({
                "name": n,
                "desc": "",
                "value": round(mean, 2),
                "share": round(mean / avg * 100, 2) if avg else 0.0,
                "cpu_count": cpu_count,
                "series": [round(v, 2) for v in name_series[n]],
            })
        return out

    items = [{"name": t["name"], "data": t["series"]} for t in _top(TOP_N)]
    if len(ranked) > TOP_N:
        others = [0.0] * len(clocks)
        for n in ranked[TOP_N:]:
            for i, v in enumerate(name_series[n]):
                others[i] += v
        items.append({"name": "其他", "data": [round(v, 2) for v in others]})

    warnings: list[str] = []
    if not has_all:
        warnings.append("未发现 CPU=all 汇总行, 已按各 CPU 求和展示")
    if len(clocks) < 2:
        warnings.append("仅 1 个采样点, 无法绘制变化趋势")
    if interval_s is None and len(clocks) >= 2:
        warnings.append("时间列无法计算采样间隔")

    return {
        "format": "sar-irq",
        "unit": "per_s",
        "metric_label": "中断/秒",
        "cpu_count": cpu_count,
        "sample_count": len(clocks),
        "interval_count": len(clocks),
        "interval_s": interval_s,
        "total": round(total, 2),
        "avg": round(avg, 2),
        "peak": peak,
        "per_cpu": per_cpu,
        "top": _top(TOP_N),
        "series": {"x": clocks, "items": items},
        "samples": [{"label": c, "total": round(t, 2)} for c, t in zip(clocks, totals)],
        "warnings": warnings,
    }


def analyze_interrupts(data: bytes, filename: str = "") -> dict:
    """中断计数变化分析入口: 自动识别 /proc/interrupts 快照 或 sar -I 输出."""
    if not data:
        raise InterruptError("文件为空")
    text = data.decode("utf-8", errors="replace")

    if any(PROC_CPU_HEADER_RE.match(ln) for ln in text.splitlines()):
        return _parse_proc(text)
    if "intr/s" in text.lower():
        return _parse_sar(text)
    raise InterruptError("未识别为 /proc/interrupts 快照或 sar -I 输出 (缺少 `CPU0 CPU1 …` 表头或 `intr/s` 表头)")
