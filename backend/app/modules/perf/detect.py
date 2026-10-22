"""性能分析数据源识别: 解压压缩包后在包内搜寻 sar / 抓包 / blktrace / 中断计数 原始数据.

设计约定:
- 解析在 pipeline 内进行, 本模块只负责「解压」与「嗅探」, 不 import 解析器算法, 避免循环依赖
- 嗅探复用各解析器内的既有正则/魔数常量 (sar.HOST_RE/KNOWN_COLS、blktrace.LINE_RE、
  interrupts.PROC_CPU_HEADER_RE、netcap.PCAP_MAGICS), 不引入第二套识别规则
- 只读文件头部 SNIFF_BYTES (默认 64KB); 头部无结论且文件不大时整读二次嗅探
"""
import gzip
import ipaddress
import re
import tarfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.diagnosis.pipeline import EXTRACT_LIMIT_FILES, safe_extract
from app.modules.perf.blktrace import LINE_RE as BLK_LINE_RE
from app.modules.perf.interrupts import PROC_CPU_HEADER_RE
from app.modules.perf.netcap import HEADER_ALIASES, PCAP_MAGICS
from app.modules.perf.sar import HOST_RE, KNOWN_COLS, TIME_RE

PERF_KINDS = ("sar", "netcap", "blktrace", "interrupts")
MAX_GROUPS_PER_KIND = 4                       # 单类候选上限 (超出记入 warnings)
PERF_MAX_FILE_BYTES = 512 * 1024 * 1024       # 单文件解析上限
SNIFF_BYTES = 64 * 1024                       # 嗅探读取长度
FULL_SNIFF_BYTES = 8 * 1024 * 1024            # 头部无结论时的整读上限
EXTRACT_MAX_DEPTH = 3                         # gzip/嵌套压缩包递归深度
INVENTORY_MAX = 50                            # 包内文件清单条数上限

GZIP_MAGIC = b"\x1f\x8b"
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"
SAR_BINARY_RE = re.compile(r"^sa\d+$", re.I)
IRQ_ROW_RE = re.compile(r"^\s*(?:LOC|NMI|ERR|\d+):\s*(?:\d+\s+)+")

# 文件名提示 (仅 +0.1 加权, 绝不作为唯一依据)
NAME_HINTS: dict[str, tuple[str, ...]] = {
    "sar": ("sar", "sysstat"),
    "netcap": ("pcap", "cap", "tshark", "tcpdump", "wire"),
    "blktrace": ("blktrace", "blkparse", "btrace"),
    "interrupts": ("interrupt", "irq", "intr"),
}


@dataclass
class Candidate:
    """某个 kind 的候选文件 (按得分降序)."""
    path: Path
    rel: str
    size: int
    score: float
    evidence: list[str] = field(default_factory=list)
    matched: int = 0


@dataclass
class DetectResult:
    """识别结论: kind 为空表示未识别到任何数据源."""
    kind: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)      # kind -> 最高证据分
    evidence: dict[str, list[str]] = field(default_factory=dict)
    files_of: dict[str, list[str]] = field(default_factory=dict)
    unsupported: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 解压
def extract_for_perf(archive: Path, dest: Path) -> tuple[list[Path], list[str]]:
    """解压并补全 safe_extract 的两个缺口: 纯 .gz 与嵌套压缩包 (深度 <= EXTRACT_MAX_DEPTH)."""
    warnings: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    files = _expand(safe_extract(archive, dest), dest, warnings, 0)
    return files, warnings


def _expand(files: list[Path], dest: Path, warnings: list[str], depth: int) -> list[Path]:
    """对解压结果递归展开: 纯 gzip 文件解压; 包内仍是 zip/tar 的继续解压."""
    out: list[Path] = []
    for f in files:
        if len(out) >= EXTRACT_LIMIT_FILES:
            warnings.append(f"包内文件过多, 仅处理前 {EXTRACT_LIMIT_FILES} 个")
            break
        if depth < EXTRACT_MAX_DEPTH and _is_gzip(f):
            target = dest / "__gz__" / _gz_name(f)
            try:
                _gunzip(f, target, PERF_MAX_FILE_BYTES)
            except OSError as e:
                warnings.append(f"跳过无法解压的 gzip 文件 {f.name}: {e}")
                continue
            if depth + 1 < EXTRACT_MAX_DEPTH and (zipfile.is_zipfile(target)
                                                 or tarfile.is_tarfile(target)):
                out.extend(_expand([target], dest, warnings, depth + 1))
            else:
                out.append(target)
            continue
        if depth < EXTRACT_MAX_DEPTH and (zipfile.is_zipfile(f) or tarfile.is_tarfile(f)):
            inner = safe_extract(f, dest / "__inner__" / f.stem)
            out.extend(_expand(inner, dest, warnings, depth + 1))
            continue
        out.append(f)
    return out


def _is_gzip(path: Path) -> bool:
    try:
        with open(path, "rb") as fp:
            return fp.read(2) == GZIP_MAGIC
    except OSError:
        return False


def _gz_name(path: Path) -> str:
    """gzip 解压后的落地文件名: foo.txt.gz -> foo.txt; foo.tgz -> foo.tar; 其它 -> foo.out."""
    low = path.name.lower()
    if low.endswith(".tgz"):
        return f"{path.name[:-4]}.tar"
    if low.endswith(".gz"):
        return path.name[:-3]
    return f"{path.name}.out"


def _gunzip(src: Path, dst: Path, limit: int) -> None:
    """流式解压 gzip, 超限即中止 (防解压炸弹)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with gzip.open(src, "rb") as fin, open(dst, "wb") as fout:
            while chunk := fin.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise OSError(f"解压后超过 {limit // 1024 // 1024}MB")
                fout.write(chunk)
    except (OSError, EOFError):
        dst.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ 嗅探
def detect_kind(files: list[Path], hint_kind: str, max_groups: int = MAX_GROUPS_PER_KIND,
                base: Path | None = None) -> DetectResult:
    """嗅探包内文件并给出结论.

    得分 = 证据分 + (与页签同类 ? 0.25 : 0); 优先取同类且 >= 0.8 的最优 kind,
    否则取全局最高分; 无任何证据判未识别。同分依次比匹配行数 -> 文件字节 -> 路径。
    """
    result = DetectResult()
    inventory: list[dict] = []
    pools: dict[str, list[Candidate]] = {k: [] for k in PERF_KINDS}

    for path in files:
        rel = _rel(path, base)
        size = _size(path)
        raw, unsupported, truncated = _sniff(path, size)
        text = _decode(raw)
        if unsupported:
            result.unsupported.append(unsupported)
        found = _collect(text, raw, rel, path, size, pools)
        if not found and truncated and 0 < size <= FULL_SNIFF_BYTES:
            # 头部无结论且文件不大: 整读二次嗅探 (数据行可能落在文件后半)
            full = _read(path, FULL_SNIFF_BYTES)
            full_raw = _gunzip_prefix(full) if full[:2] == GZIP_MAGIC else full
            if full_raw != raw and b"\x00" not in full[:2048]:
                text = _decode(full_raw)
                found = _collect(text, full_raw, rel, path, size, pools)
        best = max(found, key=lambda c: c.score, default=None)
        inventory.append({
            "path": rel, "size": size,
            "sniff": f"{best.evidence[0]} ({best.score:.1f})" if best else "",
            "preview": _preview(text),
        })

    if len(inventory) > INVENTORY_MAX:
        result.warnings.append(f"包内文件较多, 文件清单仅展示前 {INVENTORY_MAX} 个")
    result.inventory = inventory[:INVENTORY_MAX]

    for kind, items in pools.items():
        if not items:
            continue
        items.sort(key=lambda c: (-c.score, -c.matched, -c.size, c.rel))
        result.scores[kind] = items[0].score
        result.evidence[kind] = items[0].evidence
        result.files_of[kind] = [c.rel for c in items]

    chosen = _choose(result.scores, hint_kind)
    if chosen is not None:
        result.kind = chosen
        items = pools[chosen]
        result.candidates = items[:max_groups]
        if len(items) > max_groups:
            result.warnings.append(
                f"包内 {chosen} 候选共 {len(items)} 个, 超上限仅解析前 {max_groups} 个")
    return result


def _collect(text: str, raw: bytes, rel: str, path: Path, size: int,
             pools: dict[str, list[Candidate]]) -> list[Candidate]:
    """对单个文件按各 kind 打分, 命中者写入 pools; 返回本文件命中的候选."""
    found: list[Candidate] = []
    for kind in PERF_KINDS:
        score, evidence, matched = _score(kind, text, raw)
        if score <= 0:
            continue
        if _name_hint(rel, kind):
            evidence = [*evidence, f"文件名疑似 {kind}"]
            score = min(1.0, score + 0.1)
        cand = Candidate(path=path, rel=rel, size=size, score=score,
                         evidence=evidence, matched=matched)
        pools[kind].append(cand)
        found.append(cand)
    return found


def _score(kind: str, text: str, raw: bytes) -> tuple[float, list[str], int]:
    """打分入口: 二进制格式先看魔数, 其余按文本行判定."""
    if kind == "netcap":
        magic = raw[:4]
        if magic in PCAP_MAGICS:
            return 1.0, [f"pcap 文件魔数 {magic.hex()}"], 1
    if not text:
        return 0.0, [], 0
    return _SCORERS[kind](text)


def _choose(scores: dict[str, float], hint_kind: str) -> str | None:
    """择一策略: 同类优先 (含 0.25 加成), 无证据返回 None."""
    if not scores:
        return None
    weighted = {k: v + (0.25 if k == hint_kind else 0.0) for k, v in scores.items()}
    if weighted.get(hint_kind, 0.0) >= 0.8:
        return hint_kind
    best = max(weighted, key=lambda k: (weighted[k], scores[k], k == hint_kind))
    return best if weighted[best] >= 0.5 else None


def _sniff(path: Path, size: int) -> tuple[bytes, dict | None, bool]:
    """读取文件头部原始字节 (gzip 已展开); 返回 (字节, 不支持格式提示, 是否被截断)."""
    head = _read(path, SNIFF_BYTES)
    if not head:
        return b"", None, False
    data = _gunzip_prefix(head) if head[:2] == GZIP_MAGIC else head
    return data, _unsupported(path.name, data), size > len(head)


def _read(path: Path, limit: int) -> bytes:
    try:
        with open(path, "rb") as fp:
            return fp.read(limit)
    except OSError:
        return b""


def _gunzip_prefix(data: bytes) -> bytes:
    """gzip 前缀流式解压 (只取头部, 不落地)."""
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, SNIFF_BYTES)
    except zlib.error:
        return b""


def _decode(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return text[1:] if text.startswith("\ufeff") else text


def _preview(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


def _rel(path: Path, base: Path | None) -> str:
    if base is not None:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return path.name


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _name_hint(rel: str, kind: str) -> bool:
    low = rel.rsplit("/", 1)[-1].lower()
    return any(h in low for h in NAME_HINTS[kind])


# ------------------------------------------------------------------ 各类型证据
def _score_sar(text: str) -> tuple[float, list[str], int]:
    """sar 输出每行都以时刻开头, 列名行亦然; 故按「去掉首列时刻后是否含已知列名」判定."""
    has_host = False
    col_hits = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Linux"):
            if HOST_RE.match(stripped):
                has_host = True
            continue
        toks = stripped.split()
        rest = toks[1:] if toks and TIME_RE.match(toks[0]) else toks
        norm = {t.strip().lower().replace("-", "_") for t in rest}
        col_hits = max(col_hits, len(norm & KNOWN_COLS))
    if has_host and col_hits >= 3:
        return 1.0, [f"Linux 头 + {col_hits} 个 sar 列名"], col_hits
    if col_hits >= 3:
        return 0.6, [f"{col_hits} 个 sar 列名 (缺少 Linux 头)"], col_hits
    return 0.0, [], 0


def _score_netcap(text: str) -> tuple[float, list[str], int]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines:
        norm = {t.strip().strip('"').lower() for t in _split_row(lines[0])}
        hits = sum(1 for aliases in HEADER_ALIASES.values() if norm & aliases)
        if len(norm) >= 4 and hits >= 2:
            return 1.0, [f"表头命中 {hits} 个 tshark 字段"], len(lines)
    rows = ip_rows = 0
    for line in lines[:200]:
        toks = _split_row(line)
        if len(toks) < 5:
            continue
        rows += 1
        if sum(1 for t in toks if _is_ip(t)) >= 2:
            ip_rows += 1
    if rows >= 2 and ip_rows >= 2:
        return 0.6, [f"{ip_rows} 行含 >=2 个 IP 地址"], ip_rows
    return 0.0, [], 0


def _score_blktrace(text: str) -> tuple[float, list[str], int]:
    matched = sum(1 for ln in text.splitlines()[:200] if BLK_LINE_RE.match(ln))
    if matched >= 3:
        return 1.0, [f"{matched} 行 blkparse 记录"], matched
    if matched >= 1:
        return 0.6, [f"{matched} 行 blkparse 记录"], matched
    return 0.0, [], 0


def _score_interrupts(text: str) -> tuple[float, list[str], int]:
    lines = text.splitlines()
    if any(PROC_CPU_HEADER_RE.match(ln) for ln in lines[:50]):
        return 1.0, ["/proc/interrupts CPU 表头"], 1
    if "intr/s" in text:
        return 1.0, ["sar -I 的 intr/s 列"], 1
    rows = sum(1 for ln in lines[:200] if IRQ_ROW_RE.match(ln))
    if rows >= 3:
        return 0.6, [f"{rows} 行中断计数记录"], rows
    return 0.0, [], 0


_SCORERS = {"sar": _score_sar, "netcap": _score_netcap,
            "blktrace": _score_blktrace, "interrupts": _score_interrupts}


def _split_row(line: str) -> list[str]:
    """按制表符/逗号/分号切分; 无分隔符时退化为空白切分."""
    for sep in ("\t", ",", ";"):
        if sep in line:
            return line.split(sep)
    return line.split()


def _is_ip(token: str) -> bool:
    token = token.strip().strip('"')
    if not token:
        return False
    try:
        ipaddress.ip_address(token)
        return True
    except ValueError:
        return False


def _unsupported(name: str, data: bytes) -> dict | None:
    """明确的「不支持但可转换」格式, 给出具体转换命令."""
    if data[:4] == PCAPNG_MAGIC:
        return {"path": name, "kind": "netcap", "reason": "pcapng 格式不支持 (需经典 pcap)",
                "hint": "editcap -F pcap in.pcapng out.pcap"}
    head = data[:65536]
    nul_ratio = head.count(0) / len(head) if head else 0.0
    if nul_ratio > 0.1:
        if SAR_BINARY_RE.match(Path(name).name):
            return {"path": name, "kind": "sar", "reason": "sar 二进制数据文件 (sa*) 不支持",
                    "hint": "sadf -u -r -d sa15 > sar.txt"}
        if ".blktrace." in name:
            return {"path": name, "kind": "blktrace", "reason": "blktrace 原始二进制文件不支持",
                    "hint": "blkparse -i x.blktrace.0 -o x.txt"}
    return None
