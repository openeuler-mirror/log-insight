"""blktrace 结果分析: 解析 blkparse 文本输出, 统计 IOPS/吞吐/延迟分布.

支持的输入为 blkparse 默认文本行, 形如:
    8,0    0        1     0.000000000   639  Q  WS 274432 + 8 [kworker/0:1]
    8,0    0        5     0.000004000   639  D  WS 274432 + 8 [kworker/0:1]
    8,0    0        6     0.001000000     0  C  WS 274432 + 8 [0]
字段: 设备 CPU 序号 时间(秒) PID 动作 rwbs 起始扇区 + 扇区数
动作: Q=入队 D=下发 C=完成 (A/G/I/F/M/X 等仅统计不参与延迟计算)
延迟口径: Q→C 为请求总延迟, D→C 为设备服务时间, 均按 (设备, 起始扇区) FIFO 配对。
"""
import re
from collections import Counter, defaultdict

from app.modules.perf.errors import PerfAnalysisError

LINE_RE = re.compile(
    r"^\s*(\d+,\d+)\s+\d+\s+\d+\s+(\d+\.\d+)\s+\d+\s+([A-Z])\s+([A-Z]*)\s+(\d+)\s*\+\s*(\d+)"
)
SECTOR_BYTES = 512
TOP_N = 20

LATENCY_BUCKETS = [(0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 5),
                   (5, 10), (10, 50), (50, 100), (100, None)]  # 毫秒
SIZE_BUCKETS = [(0, 8, "0-4KB"), (8, 32, "4-16KB"), (32, 128, "16-64KB"),
                (128, 512, "64-256KB"), (512, None, ">256KB")]  # 扇区


class BlktraceError(PerfAnalysisError):
    """输入内容无法识别为 blkparse 输出."""


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * pct), len(sorted_vals) - 1)
    return sorted_vals[idx]


def _size_label(sectors: int) -> str:
    for lo, hi, label in SIZE_BUCKETS:
        if hi is None:
            return label
        if lo <= sectors < hi:
            return label
    return "未知"


def analyze_blktrace(data: bytes, filename: str = "") -> dict:
    """解析 blkparse 文本, 返回 IOPS/吞吐/延迟/设备维度的聚合统计."""
    if not data:
        raise BlktraceError("文件为空")
    text = data.decode("utf-8", errors="replace")

    q_map: dict[tuple, list[float]] = defaultdict(list)
    d_map: dict[tuple, list[float]] = defaultdict(list)
    q2c: list[float] = []          # 请求总延迟 (秒)
    d2c: list[float] = []          # 设备服务时间 (秒)
    devices: dict[str, dict] = defaultdict(lambda: {"r": 0, "w": 0, "bytes": 0})
    read_count = write_count = 0
    total_bytes = 0
    size_hist: Counter = Counter()
    timeline: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # ops, bytes, latency_sum
    slow: list[dict] = []
    times: list[float] = []
    matched = 0
    lines_parsed = 0

    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        dev, t, action, rwbs, start, nr = m.groups()
        t = float(t)
        start, nr = int(start), int(nr)
        bytes_ = nr * SECTOR_BYTES
        rw = "W" if rwbs.startswith("W") else "R" if rwbs.startswith("R") else "?"
        key = (dev, start)
        lines_parsed += 1
        times.append(t)

        if action == "Q":
            q_map[key].append(t)
        elif action == "D":
            d_map[key].append(t)
            if rw == "W":
                write_count += 1
                devices[dev]["w"] += 1
            else:
                read_count += 1
                devices[dev]["r"] += 1
            devices[dev]["bytes"] += bytes_
            total_bytes += bytes_
            size_hist[_size_label(nr)] += 1
            b = timeline[int(t)]
            b[0] += 1
            b[1] += bytes_
        elif action == "C":
            if not q_map[key]:
                continue
            q_t = q_map[key].pop(0)
            total_lat = t - q_t
            q2c.append(total_lat)
            matched += 1
            if d_map[key]:
                d2c.append(t - d_map[key].pop(0))
            timeline[int(t)][2] += total_lat
            lat_ms = total_lat * 1000
            slow.append({"dev": dev, "sector": start, "bytes": bytes_, "rw": rw,
                         "latency_ms": round(lat_ms, 3), "time": round(t, 6)})

    if lines_parsed == 0:
        raise BlktraceError("未解析到任何 blkparse 记录 (请确认输入为 blkparse 文本输出)")

    warnings: list[str] = []
    if matched == 0:
        warnings.append("未找到配对的 Q→C 记录 (trace 可能被截断或仅含部分阶段), 延迟统计为空")
    if not total_bytes and read_count + write_count == 0:
        warnings.append("未发现 D (下发) 阶段记录, 吞吐/IOPS 可能为 0")

    q2c.sort()
    d2c.sort()
    duration = (max(times) - min(times)) if times else 0.0
    lat_ms = [v * 1000 for v in q2c]
    slow.sort(key=lambda x: x["latency_ms"], reverse=True)

    tl_items = sorted(timeline.items())
    return {
        "format": "blkparse",
        "record_count": lines_parsed,
        "op_count": read_count + write_count,
        "read_count": read_count,
        "write_count": write_count,
        "total_bytes": total_bytes,
        "duration_s": round(duration, 6),
        "iops": round((read_count + write_count) / duration, 2) if duration > 0 else 0.0,
        "throughput_mbps": round(total_bytes / duration / 1024 / 1024, 3) if duration > 0 else 0.0,
        "latency": {
            "count": len(q2c),
            "avg_ms": round(sum(lat_ms) / len(lat_ms), 3) if lat_ms else 0.0,
            "p50_ms": round(_percentile(lat_ms, 0.50), 3),
            "p90_ms": round(_percentile(lat_ms, 0.90), 3),
            "p99_ms": round(_percentile(lat_ms, 0.99), 3),
            "max_ms": round(lat_ms[-1], 3) if lat_ms else 0.0,
        },
        "service_ms": {
            "count": len(d2c),
            "avg_ms": round(sum(d2c) / len(d2c) * 1000, 3) if d2c else 0.0,
            "p99_ms": round(_percentile([v * 1000 for v in d2c], 0.99), 3),
        },
        "devices": [{"dev": d, "r": v["r"], "w": v["w"], "ops": v["r"] + v["w"],
                     "bytes": v["bytes"]}
                    for d, v in sorted(devices.items(), key=lambda kv: kv[1]["bytes"], reverse=True)],
        "latency_histogram": _latency_histogram(lat_ms),
        "size_histogram": [{"bucket": label, "count": size_hist.get(label, 0)}
                           for _, _, label in SIZE_BUCKETS],
        "timeline": [{"t": ts, "ops": int(v[0]), "bytes": int(v[1]),
                      "avg_latency_ms": round(v[2] / v[0] * 1000, 3) if v[0] else 0.0}
                     for ts, v in tl_items],
        "slow_ios": slow[:TOP_N],
        "warnings": warnings,
    }


def _latency_histogram(lat_ms: list[float]) -> list[dict]:
    """延迟直方图 (按毫秒分桶)."""
    out = []
    for lo, hi in LATENCY_BUCKETS:
        if hi is None:
            out.append({"bucket": f">{lo}ms", "count": sum(1 for v in lat_ms if v >= lo)})
        else:
            out.append({"bucket": f"{lo}-{hi}ms", "count": sum(1 for v in lat_ms if lo <= v < hi)})
    return out