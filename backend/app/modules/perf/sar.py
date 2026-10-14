"""sar 文本输出分析: 解析 sar -u / -r / -d (可选 -q) 文本, 输出前端可直接渲染的聚合结果.

支持输入: `LC_ALL=C sar -u -r -d 1 10 > sar.txt` 的文本输出 (含 Average 行亦可)
不支持: sar 二进制数据文件 (sa*) —— 请先用 `sadf -u -r -d` 或 `sar -f` 转文本
设计: 表驱动分节识别 (SECTION_COLS), 新增视图只需追加列定义, 不改主循环
"""
import re

from app.modules.perf.errors import PerfAnalysisError

POINTS_MAX = 2000          # 单视图时序点上限, 超出按等间隔抽稀
CPU_PER_CPU_TOP_N = 20     # per-CPU 摘要表行数上限
DEVICE_SERIES_TOP_N = 8    # 磁盘图中最多绘制的设备数
DEVICE_TABLE_MAX = 50      # 磁盘设备表最多行数
SECTOR_KB = 2              # 扇区/秒 -> KB/s: v * 512 / 1024 = v / 2

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM|上午|下午)?", re.I)
MERIDIANS = ("AM", "PM", "上午", "下午")  # sar 输出中紧随时刻的独立 token
HOST_RE = re.compile(r"^Linux\s+(\S+)\s+\(([^)]+)\)\s+(\d{2}/\d{2}/\d{4})")
CPU_NUM_RE = re.compile(r"\((\d+)\s+CPU\)")

# 归一化列名 (lower, '-' -> '_') -> 输出字段名
CPU_COLS = {
    "cpu": "cpu",  # -P ALL 时的 per-CPU 列 (值: all / 0 / 1 ...)
    "%user": "user", "%nice": "nice", "%system": "system", "%iowait": "iowait",
    "%steal": "steal", "%idle": "idle", "%irq": "irq", "%softirq": "softirq",
    "%guest": "guest", "%gnice": "gnice",
}
MEM_COLS = {
    "kbmemfree": "mem_free_kb", "kbavail": "mem_avail_kb", "kbmemused": "mem_used_kb",
    "%memused": "mem_used_pct", "kbbuffers": "buffers_kb", "kbcached": "cached_kb",
}
DISK_COLS = {
    "dev": "dev", "tps": "tps",
    "rd_sec/s": "read_sec_s", "wr_sec/s": "write_sec_s",     # 扇区/秒 (需 /2)
    "rkb/s": "read_kbps", "wkb/s": "write_kbps",             # KB/秒 (无需换算)
    "areq_sz": "avgrq_sz", "avgrq_sz": "avgrq_sz",
    "aqu_sz": "avgqu_sz", "avgqu_sz": "avgqu_sz",
    "await": "await_ms", "svctm": "svctm_ms", "%util": "util_pct",
}
LOAD_COLS = {
    "runq_sz": "runq_sz", "plist_sz": "plist_sz",
    "ldavg_1": "ldavg_1", "ldavg_5": "ldavg_5", "ldavg_15": "ldavg_15",
    "blocked": "blocked", "%block": "blocked",
}
SECTION_COLS = {"cpu": CPU_COLS, "mem": MEM_COLS, "disk": DISK_COLS, "load": LOAD_COLS}
KNOWN_COLS = {k for cols in SECTION_COLS.values() for k in cols}
VIEW_ORDER = ("cpu", "mem", "disk", "load")


class SarError(PerfAnalysisError):
    """输入内容无法识别为受支持的 sar 文本输出."""


def _norm(tok: str) -> str:
    return tok.strip().lower().replace("-", "_")


def _to_float(tok: str | None) -> float:
    if tok is None:
        return 0.0
    s = tok.strip().rstrip("%").replace(",", "")
    if not s or s in ("-", "N/A", "nan"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _opt_float(tok: str | None) -> float | None:
    """可选数值: 列不存在或不可解析时返回 None."""
    if tok is None:
        return None
    s = tok.strip().rstrip("%").replace(",", "")
    if not s or s in ("-", "N/A", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_clock(tok: str, meridian: str = "") -> int | None:
    """HH:MM:SS [AM/PM] -> 当日秒数 (统一归一化为 24 小时制); 非法返回 None."""
    m = TIME_RE.match(tok)
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
    ap = (m.group(4) or meridian or "").upper()
    if ap in ("PM", "下午") and h < 12:
        h += 12
    elif ap in ("AM", "上午") and h == 12:
        h = 0
    if h > 23 or mi > 59 or se > 59:
        return None
    return h * 3600 + mi * 60 + se


def _fmt_clock(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    mi, se = divmod(rem, 60)
    return f"{h:02d}:{mi:02d}:{se:02d}"


def _detect_section(norms: list[str]) -> str | None:
    s = set(norms)
    if "%user" in s and "%idle" in s:
        return "cpu"
    if "%memused" in s or "kbmemfree" in s or "kbmemused" in s or "kbavail" in s:
        return "mem"
    if "tps" in s and "dev" in s:
        return "disk"
    if "runq_sz" in s or "ldavg_1" in s or "plist_sz" in s:
        return "load"
    return None


def _col(col_map: dict[str, int], tokens: list[str], name: str) -> str | None:
    idx = col_map.get(name)
    if idx is None or idx >= len(tokens):
        return None
    return tokens[idx]


def _thin(points: list[dict]) -> tuple[list[dict], bool]:
    n = len(points)
    if n <= POINTS_MAX:
        return points, False
    step = -(-n // POINTS_MAX)  # ceil
    return points[::step], True


def _avg(vals: list[float]) -> float:
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def analyze_sar(data: bytes, filename: str = "") -> dict:
    if not data:
        raise SarError("文件为空")
    if b"\x00" in data[:65536]:
        raise SarError("疑似 sar 二进制数据文件(sa*)，请先用 `sadf -u -r -d` 转为文本后再上传")

    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]

    meta = {"host": "", "kernel": "", "date": "", "cpu_count": 0}
    cpu_all: list[dict] = []
    cpu_each: dict[str, list[dict]] = {}
    mem_rows: list[dict] = []
    disk_rows: list[dict] = []
    load_rows: list[dict] = []
    unrecognized: dict[str, int] = {}
    skipped = 0
    section: str | None = None
    col_map: dict[str, int] = {}
    last_t = 0.0
    day_offset = 0.0
    all_t: list[float] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Linux"):
            m = HOST_RE.match(line)
            if m:
                meta["kernel"], meta["host"], meta["date"] = m.group(1), m.group(2), m.group(3)
            m2 = CPU_NUM_RE.search(line)
            if m2:
                meta["cpu_count"] = int(m2.group(1))
            continue

        tokens = line.split()
        meridian = tokens[1] if len(tokens) > 1 and tokens[1].upper() in MERIDIANS else ""
        sec_of_day = _parse_clock(tokens[0], meridian)
        if sec_of_day is None:
            continue  # Average / 平均时间 / 其它说明行

        norms = [_norm(t) for t in tokens[1:]]
        if any(t in KNOWN_COLS for t in norms):
            sec = _detect_section(norms)
            if sec:
                section = sec
                cols = SECTION_COLS[sec]
                col_map = {t: i + 1 for i, t in enumerate(norms) if t in cols}
                last_t = 0.0       # 各数据段时刻序列独立, 段首重置 (避免跨段误判零点回退)
                day_offset = 0.0
            else:
                key = " ".join(tokens[1:5])
                unrecognized[key] = unrecognized.get(key, 0) + 1
                section = None
                col_map = {}
            continue

        if section is None:
            continue

        # 数据行: 累计秒 (跨零点回退时 +86400, 保证单调)
        t = day_offset + sec_of_day
        if t < last_t:
            day_offset += 86400
            t = day_offset + sec_of_day
        last_t = t
        clock = _fmt_clock(sec_of_day)

        if section == "cpu":
            name = _col(col_map, tokens, "cpu") or "all"
            row = {
                "t": t, "clock": clock, "cpu": name,
                "user": _to_float(_col(col_map, tokens, "%user")),
                "nice": _to_float(_col(col_map, tokens, "%nice")),
                "system": _to_float(_col(col_map, tokens, "%system")),
                "iowait": _to_float(_col(col_map, tokens, "%iowait")),
                "steal": _to_float(_col(col_map, tokens, "%steal")),
                "idle": _to_float(_col(col_map, tokens, "%idle")),
                "irq": _to_float(_col(col_map, tokens, "%irq")) + _to_float(_col(col_map, tokens, "%softirq")),
            }
            if name == "all":
                cpu_all.append(row)
            else:
                cpu_each.setdefault(name, []).append(row)
            all_t.append(t)

        elif section == "mem":
            free_kb = _to_float(_col(col_map, tokens, "kbmemfree"))
            used_kb = _to_float(_col(col_map, tokens, "kbmemused"))
            pct = _opt_float(_col(col_map, tokens, "%memused"))
            if pct is None:
                total = used_kb + free_kb
                pct = round(used_kb / total * 100, 2) if total > 0 else 0.0
            mem_rows.append({
                "t": t, "clock": clock,
                "mem_free_kb": free_kb,
                "mem_avail_kb": _opt_float(_col(col_map, tokens, "kbavail")),
                "mem_used_kb": used_kb,
                "mem_used_pct": pct,
                "buffers_kb": _to_float(_col(col_map, tokens, "kbbuffers")),
                "cached_kb": _to_float(_col(col_map, tokens, "kbcached")),
            })
            all_t.append(t)

        elif section == "disk":
            dev = _col(col_map, tokens, "dev")
            if dev is None:
                skipped += 1
                continue
            read_raw = _opt_float(_col(col_map, tokens, "rd_sec/s"))
            read_kbps = read_raw / SECTOR_KB if read_raw is not None else (
                _opt_float(_col(col_map, tokens, "rkb/s")) or 0.0)
            write_raw = _opt_float(_col(col_map, tokens, "wr_sec/s"))
            write_kbps = write_raw / SECTOR_KB if write_raw is not None else (
                _opt_float(_col(col_map, tokens, "wkb/s")) or 0.0)
            disk_rows.append({
                "t": t, "clock": clock, "dev": dev,
                "tps": _to_float(_col(col_map, tokens, "tps")),
                "read_kbps": round(read_kbps, 2),
                "write_kbps": round(write_kbps, 2),
                "avgrq_sz": _to_float(_col(col_map, tokens, "avgrq_sz") or _col(col_map, tokens, "areq_sz")),
                "avgqu_sz": _to_float(_col(col_map, tokens, "avgqu_sz") or _col(col_map, tokens, "aqu_sz")),
                "await_ms": _to_float(_col(col_map, tokens, "await")),
                "svctm_ms": _to_float(_col(col_map, tokens, "svctm")),
                "util_pct": _to_float(_col(col_map, tokens, "%util")),
            })
            all_t.append(t)

        else:  # load
            load_rows.append({
                "t": t, "clock": clock,
                "runq_sz": _to_float(_col(col_map, tokens, "runq_sz")),
                "plist_sz": _to_float(_col(col_map, tokens, "plist_sz")),
                "ldavg_1": _to_float(_col(col_map, tokens, "ldavg_1")),
                "ldavg_5": _to_float(_col(col_map, tokens, "ldavg_5")),
                "ldavg_15": _to_float(_col(col_map, tokens, "ldavg_15")),
                "blocked": _to_float(_col(col_map, tokens, "blocked")),
            })
            all_t.append(t)

    # 汇总统计
    warnings: list[str] = []
    interval_s = 0.0
    if all_t:
        uniq = sorted(set(all_t))
        diffs = [round(b - a, 3) for a, b in zip(uniq, uniq[1:]) if b > a]
        interval_s = min(diffs) if diffs else 0.0

    views: list[str] = []
    cpu_out = mem_out = disk_out = load_out = None

    if cpu_all:
        views.append("cpu")
        cpu_out = _agg_cpu(cpu_all, cpu_each, warnings)
    elif cpu_each:
        views.append("cpu")
        first = next(iter(cpu_each.values()))
        cpu_out = _agg_cpu(first, cpu_each, warnings)
        warnings.append("未找到 CPU=all 汇总行, 已退化为使用首个 per-CPU 序列")

    if mem_rows:
        views.append("mem")
        mem_out = _agg_mem(mem_rows, warnings)

    if disk_rows:
        views.append("disk")
        disk_out = _agg_disk(disk_rows, interval_s, warnings)

    if load_rows:
        views.append("load")
        load_out = _agg_simple(load_rows, ("runq_sz", "ldavg_1", "ldavg_5", "ldavg_15"))

    if not views:
        hint = "；".join(f"{k} (出现 {v} 次)" for k, v in list(unrecognized.items())[:3])
        raise SarError(
            "未识别到 sar 数据段 (需要 sar -u / -r / -d 的文本输出)"
            + (f"；未识别表头: {hint}" if hint else "")
            + "；本地化环境建议使用 `LC_ALL=C sar ...`"
        )

    if unrecognized:
        hint = "，".join(f"`{k}`" for k in list(unrecognized)[:3])
        warnings.append(f"存在 {len(unrecognized)} 类未识别的表头（{hint}），已跳过；本地化环境建议 `LC_ALL=C sar ...`")
    if skipped:
        warnings.append(f"跳过 {skipped} 行形状不符的数据行")

    start_clock = _fmt_clock(int(min(all_t))) if all_t else ""
    end_clock = _fmt_clock(int(max(all_t))) if all_t else ""

    return {
        "format": "sar",
        "host": meta["host"],
        "kernel": meta["kernel"],
        "date": meta["date"],
        "cpu_count": meta["cpu_count"],
        "interval_s": interval_s,
        "duration_s": round(max(all_t) - min(all_t), 3) if all_t else 0.0,
        "start_clock": start_clock,
        "end_clock": end_clock,
        "views": views,
        "cpu": cpu_out,
        "mem": mem_out,
        "disk": disk_out,
        "load": load_out,
        "warnings": warnings,
    }


def _agg_simple(rows: list[dict], fields: tuple[str, ...]) -> dict:
    points, thinned = _thin(rows)
    avg = {f: _avg([r[f] for r in rows]) for f in fields}
    peak_val, peak_clock = max((r[fields[0]], r["clock"]) for r in rows)
    return {
        "points": points,
        "avg": avg,
        "peak": {"value": peak_val, "clock": peak_clock},
        "points_total": len(rows),
        "points_returned": len(points),
        "thinned": thinned,
    }


def _agg_cpu(rows: list[dict], cpu_each: dict[str, list[dict]], warnings: list[str]) -> dict:
    fields = ("user", "nice", "system", "iowait", "steal", "idle", "irq")
    points, thinned = _thin(rows)
    avg = {f: _avg([r[f] for r in rows]) for f in fields}
    avg_busy = round(100 - avg["idle"], 2)
    peak = max(rows, key=lambda r: 100 - r["idle"])
    peak_row = {"busy": round(100 - peak["idle"], 2), "busy_clock": peak["clock"],
                "iowait": max(r["iowait"] for r in rows)}
    per_cpu = []
    if cpu_each:
        for name, rs in cpu_each.items():
            per_cpu.append({
                "cpu": name,
                "avg_busy": round(100 - _avg([r["idle"] for r in rs]), 2),
                "avg_idle": _avg([r["idle"] for r in rs]),
                "point_count": len(rs),
            })
        per_cpu.sort(key=lambda d: d["avg_busy"], reverse=True)
        per_cpu = per_cpu[:CPU_PER_CPU_TOP_N]
        warnings.append(f"检测到 {len(cpu_each)} 个 per-CPU 序列，主图仅绘制 all 汇总，明细见 per-CPU 表")
    return {
        "points": points,
        "avg": {**avg, "busy": avg_busy},
        "peak": peak_row,
        "per_cpu": per_cpu,
        "points_total": len(rows),
        "points_returned": len(points),
        "thinned": thinned,
    }


def _agg_mem(rows: list[dict], warnings: list[str]) -> dict:
    points, thinned = _thin(rows)
    avg = {
        "mem_used_pct": _avg([r["mem_used_pct"] for r in rows]),
        "mem_used_kb": _avg([r["mem_used_kb"] for r in rows]),
        "mem_free_kb": _avg([r["mem_free_kb"] for r in rows]),
    }
    peak = max(rows, key=lambda r: r["mem_used_pct"])
    mem_total_kb = 0
    for r in rows:
        if r["mem_used_pct"] > 0:
            mem_total_kb = int(r["mem_used_kb"] / (r["mem_used_pct"] / 100))
            break
    if not mem_total_kb:
        mem_total_kb = int(max(r["mem_used_kb"] + r["mem_free_kb"] for r in rows))
        warnings.append("未解析到显式内存总量，已用 used+free 估算")
    return {
        "points": points,
        "avg": avg,
        "peak": {"mem_used_pct": peak["mem_used_pct"], "mem_used_pct_clock": peak["clock"],
                 "mem_used_kb": peak["mem_used_kb"]},
        "mem_total_kb": mem_total_kb,
        "points_total": len(rows),
        "points_returned": len(points),
        "thinned": thinned,
    }


def _agg_disk(rows: list[dict], interval_s: float, warnings: list[str]) -> dict:
    by_dev: dict[str, list[dict]] = {}
    for r in rows:
        by_dev.setdefault(r["dev"], []).append(r)

    step = interval_s if interval_s > 0 else 1.0
    devices = []
    for dev, rs in by_dev.items():
        devices.append({
            "dev": dev,
            "point_count": len(rs),
            "avg_tps": _avg([r["tps"] for r in rs]),
            "avg_read_kbps": _avg([r["read_kbps"] for r in rs]),
            "avg_write_kbps": _avg([r["write_kbps"] for r in rs]),
            "avg_await_ms": _avg([r["await_ms"] for r in rs]),
            "max_await_ms": round(max(r["await_ms"] for r in rs), 2),
            "avg_util_pct": _avg([r["util_pct"] for r in rs]),
            "max_util_pct": round(max(r["util_pct"] for r in rs), 2),
            "total_read_kb": round(sum(r["read_kbps"] for r in rs) * step),
            "total_write_kb": round(sum(r["write_kbps"] for r in rs) * step),
        })
    devices.sort(key=lambda d: d["max_util_pct"], reverse=True)

    top_devs = [d["dev"] for d in devices[:DEVICE_SERIES_TOP_N]]
    series = []
    for dev in top_devs:
        pts, _ = _thin(by_dev[dev])
        series.append({"dev": dev, "points": pts})

    peak_read = max(rows, key=lambda r: r["read_kbps"])
    peak_write = max(rows, key=lambda r: r["write_kbps"])
    peak_util = max(rows, key=lambda r: r["util_pct"])
    peak_await = max(rows, key=lambda r: r["await_ms"])
    if len(devices) > DEVICE_TABLE_MAX:
        warnings.append(f"设备较多({len(devices)})，设备表仅展示前 {DEVICE_TABLE_MAX} 行")
    if len(by_dev) > DEVICE_SERIES_TOP_N:
        warnings.append(f"磁盘图仅绘制 %util 最高的前 {DEVICE_SERIES_TOP_N} 个设备，其余见设备表")

    return {
        "devices": devices[:DEVICE_TABLE_MAX],
        "series": series,
        "totals": {
            "tps": round(sum(d["avg_tps"] for d in devices), 2),
            "read_kbps_peak": peak_read["read_kbps"],
            "write_kbps_peak": peak_write["write_kbps"],
            "max_util_pct": peak_util["util_pct"],
            "max_util_dev": peak_util["dev"],
            "max_await_ms": peak_await["await_ms"],
            "max_await_dev": peak_await["dev"],
        },
        "throughput_unit": "KB/s",
        "device_total": len(devices),
        "series_devices": len(series),
    }