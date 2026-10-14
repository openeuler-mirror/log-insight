"""网络抓包结果分析: 支持 pcap 原始文件与 tshark 字段导出文本两种输入.

- pcap 二进制: 纯 Python 解析全局头 + 逐包记录头, 支持下述链路层 (Ethernet / VLAN / Linux SLL / 裸 IP)
- tshark 文本: 制表符或逗号分隔, 支持带表头(按列名识别)或不带表头(按固定顺序)两种
  固定顺序: 时间, 源IP, 目的IP, 协议, 长度, 源端口, 目的端口
输出聚合结果: 总量/速率、协议分布、Top 源/目的、会话 Top、包长直方图、逐秒时序。
"""
import ipaddress
import struct
from collections import Counter, defaultdict

PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),        # 小端, 微秒
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),        # 大端, 微秒
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),    # 小端, 纳秒
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),    # 大端, 纳秒
}
IP_PROTO = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP", 58: "ICMPv6", 132: "SCTP"}
SIZE_BUCKETS = [(0, 64, "0-64"), (64, 128, "64-128"), (128, 256, "128-256"),
                (256, 512, "256-512"), (512, 1024, "512-1024"),
                (1024, 1518, "1024-1518"), (1518, None, ">1518")]
TOP_N = 15
TIMELINE_MAX = 2000  # 逐秒时序点数上限, 超出按等间隔合并

# tshark 字段导出列名别名 (归一化后匹配)
HEADER_ALIASES = {
    "time": {"frame.time_epoch", "timestamp", "time", "frame.time_relative", "frame.time"},
    "src": {"ip.src", "src", "source", "src_ip", "ip.src_host"},
    "dst": {"ip.dst", "dst", "destination", "dst_ip", "ip.dst_host"},
    "proto": {"_ws.col.protocol", "protocol", "proto", "frame.protocols"},
    "len": {"frame.len", "length", "len", "frame.cap_len"},
    "sport": {"tcp.srcport", "udp.srcport", "sport", "srcport"},
    "dport": {"tcp.dstport", "udp.dstport", "dport", "dstport"},
}


class NetcapError(ValueError):
    """输入文件无法识别为受支持的抓包格式."""


def _bucket_label(length: int) -> str:
    for lo, hi, label in SIZE_BUCKETS:
        if hi is None:
            return label
        if lo <= length < hi:
            return label
    return "未知"


def _proto_name(raw: str | None) -> str:
    if not raw:
        return "未知"
    # frame.protocols 形如 eth:ethertype:ip:tcp, 取最后一段
    return raw.split(":")[-1].strip().upper() or "未知"


def _extract_ip(frame: bytes, network: int):
    """从链路层帧中提取 (src, dst, proto, sport, dport); 不识别时返回 None."""
    if network == 1:  # Ethernet
        if len(frame) < 14:
            return None
        ethertype = struct.unpack(">H", frame[12:14])[0]
        offset = 14
        if ethertype == 0x8100 and len(frame) >= 18:  # VLAN 单层
            ethertype = struct.unpack(">H", frame[16:18])[0]
            offset = 18
    elif network == 113:  # Linux cooked capture (SLL)
        if len(frame) < 16:
            return None
        ethertype = struct.unpack(">H", frame[14:16])[0]
        offset = 16
    elif network in (101, 12, 14):  # 裸 IP
        ethertype = 0x0800 if frame[0] >> 4 == 4 else 0x86DD
        offset = 0
    else:
        return None

    if ethertype == 0x0800:  # IPv4
        if len(frame) < offset + 20:
            return None
        ihl = (frame[offset] & 0x0F) * 4
        proto = frame[offset + 9]
        src = str(ipaddress.IPv4Address(frame[offset + 12:offset + 16]))
        dst = str(ipaddress.IPv4Address(frame[offset + 16:offset + 20]))
        t = offset + ihl
    elif ethertype == 0x86DD:  # IPv6
        if len(frame) < offset + 40:
            return None
        proto = frame[offset + 6]
        src = str(ipaddress.IPv6Address(frame[offset + 8:offset + 24]))
        dst = str(ipaddress.IPv6Address(frame[offset + 24:offset + 40]))
        t = offset + 40
    else:
        return None

    sport = dport = 0
    if proto in (6, 17) and len(frame) >= t + 4:
        sport, dport = struct.unpack(">HH", frame[t:t + 4])
    name = IP_PROTO.get(proto, f"IP-{proto}")
    return src, dst, name, sport, dport


def _parse_pcap(data: bytes) -> tuple[list[dict], list[str]]:
    endian, subdiv = PCAP_MAGICS[data[:4]]
    records: list[dict] = []
    warnings: list[str] = []
    if len(data) < 24:
        raise NetcapError("pcap 文件不完整 (缺少全局头)")
    network = struct.unpack(endian + "I", data[20:24])[0]
    rec = struct.Struct(endian + "IIII")
    pos, n = 24, len(data)
    truncated = False
    while pos + 16 <= n:
        ts_sec, ts_frac, incl_len, orig_len = rec.unpack_from(data, pos)
        pos += 16
        if pos + incl_len > n:
            truncated = True
            break
        frame = data[pos:pos + incl_len]
        pos += incl_len
        parsed = _extract_ip(frame, network)
        if parsed is None:
            continue
        src, dst, proto, sport, dport = parsed
        records.append({
            "t": ts_sec + ts_frac / subdiv,
            "length": orig_len or incl_len,
            "src": src, "dst": dst, "proto": proto, "sport": sport, "dport": dport,
        })
    if truncated:
        warnings.append("文件末尾存在被截断的数据包, 已忽略")
    if network not in (1, 113, 101, 12, 14):
        warnings.append(f"未识别的链路层类型 (network={network}), 全部数据包已跳过")
    return records, warnings


def _parse_text(data: bytes) -> tuple[list[dict], list[str]]:
    text = data.decode("utf-8", errors="replace")
    lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln and not ln.startswith("#")]
    if not lines:
        raise NetcapError("文本内容为空")

    delim = "\t" if "\t" in lines[0] else ","
    first = lines[0].split(delim)
    has_header = delim in lines[0] and any(c.isalpha() for c in first[0])
    col: dict[str, int] = {}
    if has_header:
        norm = [c.strip().strip('"').lower() for c in first]
        for key, aliases in HEADER_ALIASES.items():
            for i, name in enumerate(norm):
                if name in aliases:
                    col[key] = i
                    break
        body = lines[1:]
    else:  # 固定顺序: 时间, 源, 目的, 协议, 长度, 源端口, 目的端口
        for i, key in enumerate(["time", "src", "dst", "proto", "len", "sport", "dport"]):
            col[key] = i
        body = lines

    if "src" not in col or "dst" not in col:
        raise NetcapError("无法识别列: 需要包含 源IP/目的IP (或提供表头)")

    records: list[dict] = []
    warnings: list[str] = []
    skipped = 0
    for line in body:
        parts = [p.strip().strip('"') for p in line.split(delim)]

        def pick(key: str) -> str:
            i = col.get(key, -1)
            return parts[i] if 0 <= i < len(parts) else ""

        src, dst = pick("src"), pick("dst")
        try:
            ipaddress.ip_address(src)
            ipaddress.ip_address(dst)
        except ValueError:
            skipped += 1
            continue
        try:
            length = int(float(pick("len") or 0))
        except ValueError:
            length = 0
        try:
            t = float(pick("time"))
        except ValueError:
            t = 0.0

        def port(key: str) -> int:
            try:
                return int(float(pick(key)))
            except ValueError:
                return 0

        records.append({
            "t": t, "length": length, "src": src, "dst": dst,
            "proto": _proto_name(pick("proto")), "sport": port("sport"), "dport": port("dport"),
        })
    if skipped:
        warnings.append(f"跳过 {skipped} 行无法识别 IP 的记录")
    return records, warnings


def _aggregate(records: list[dict], fmt: str, warnings: list[str]) -> dict:
    total_bytes = sum(r["length"] for r in records)
    proto_pkts: Counter = Counter()
    proto_bytes: Counter = Counter()
    src_pkts: Counter = Counter()
    src_bytes: Counter = Counter()
    dst_pkts: Counter = Counter()
    dst_bytes: Counter = Counter()
    conv_pkts: Counter = Counter()
    conv_bytes: Counter = Counter()
    conv_proto: dict = {}
    size_hist: Counter = Counter()
    timeline: dict[int, list[int]] = defaultdict(lambda: [0, 0])

    times = [r["t"] for r in records if r["t"] > 0]
    for r in records:
        p, b = r["proto"], r["length"]
        proto_pkts[p] += 1
        proto_bytes[p] += b
        src_pkts[r["src"]] += 1
        src_bytes[r["src"]] += b
        dst_pkts[r["dst"]] += 1
        dst_bytes[r["dst"]] += b
        key = (r["src"], r["sport"], r["dst"], r["dport"], p)
        conv_pkts[key] += 1
        conv_bytes[key] += b
        conv_proto[key] = p
        size_hist[_bucket_label(b)] += 1
        if r["t"] > 0:
            bucket = timeline[int(r["t"])]
            bucket[0] += 1
            bucket[1] += b

    start, end = (min(times), max(times)) if times else (0.0, 0.0)
    duration = max(end - start, 0.0)

    tl_items = sorted(timeline.items())
    if len(tl_items) > TIMELINE_MAX:  # 点数过多则按等间隔合并, 保证图表可用
        step = len(tl_items) // TIMELINE_MAX + 1
        merged: dict[int, list[int]] = {}
        for ts, (cnt, byt) in tl_items:
            k = ts - (ts % step)
            agg = merged.setdefault(k, [0, 0])
            agg[0] += cnt
            agg[1] += byt
        tl_items = sorted(merged.items())

    return {
        "format": fmt,
        "packet_count": len(records),
        "byte_count": total_bytes,
        "start_time": start,
        "end_time": end,
        "duration_s": round(duration, 3),
        "pps": round(len(records) / duration, 2) if duration > 0 else 0.0,
        "bps": round(total_bytes * 8 / duration, 2) if duration > 0 else 0.0,
        "protocols": [{"name": k, "packets": proto_pkts[k], "bytes": proto_bytes[k]}
                      for k in sorted(proto_pkts, key=lambda x: proto_bytes[x], reverse=True)],
        "top_src": [{"host": h, "packets": src_pkts[h], "bytes": src_bytes[h]}
                    for h in sorted(src_pkts, key=lambda x: src_bytes[x], reverse=True)[:TOP_N]],
        "top_dst": [{"host": h, "packets": dst_pkts[h], "bytes": dst_bytes[h]}
                    for h in sorted(dst_pkts, key=lambda x: dst_bytes[x], reverse=True)[:TOP_N]],
        "conversations": [{
            "src": k[0], "sport": k[1], "dst": k[2], "dport": k[3], "proto": conv_proto[k],
            "packets": conv_pkts[k], "bytes": conv_bytes[k],
        } for k in sorted(conv_pkts, key=lambda x: conv_bytes[x], reverse=True)[:TOP_N]],
        "size_histogram": [{"bucket": label, "count": size_hist.get(label, 0)}
                           for _, _, label in SIZE_BUCKETS],
        "timeline": [{"t": ts, "packets": c, "bytes": b} for ts, (c, b) in tl_items],
        "warnings": warnings,
    }


def analyze_netcap(data: bytes, filename: str = "") -> dict:
    """解析抓包文件并返回聚合统计; 输入为空或格式不支持时抛 NetcapError."""
    if not data:
        raise NetcapError("文件为空")
    if data[:4] in PCAP_MAGICS:
        records, warnings = _parse_pcap(data)
        fmt = "pcap"
    else:
        records, warnings = _parse_text(data)
        fmt = "tshark-text"
    if not records:
        raise NetcapError("未从文件中解析出任何 IP 数据包 (请确认导出内容包含 IP 层字段)")
    return _aggregate(records, fmt, warnings)