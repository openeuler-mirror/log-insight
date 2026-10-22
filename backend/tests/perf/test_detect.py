"""性能分析: 压缩包解压与数据源识别 (app.modules.perf.detect) 单元测试

    :Preparation
        1. 无需外部基础设施, 仅用 tmp_path 构造压缩包与原始文件, 不触碰 DB/Redis;
        2. 样本: sar 文本(含 Linux 头)、sar 无头文本(仅列名)、blkparse 文本、
           tshark 文本表头、经典 pcap 魔数、pcapng 魔数、sar 二进制 sa15、无关文本;
        3. 打包方式: tar.gz / zip / 纯 .gz / 包内再嵌 zip。

    :TestStep
        1. 解压 tar.gz 包, 校验包内文件逐个展开并可用 base 得到相对路径;
        2. 校验纯 .gz 与嵌套 zip 被递归解压 (safe_extract 的两个缺口);
        3. 按各页签 hint 嗅探, 校验 kind 与得分 (强证据 1.0 / 弱证据 0.6 + 同类加成);
        4. 校验 pcapng / sa15 归入 unsupported 并给出转换命令;
        5. 校验同类优先: 包内只有 blktrace 数据时 sar 页签得到 kind=blktrace 且无候选;
        6. 校验单类候选上限与未识别场景。

    :ExpectResult
        1. 解出文件数与内容符合预期; 相对路径形如 sar.txt;
        2. foo.txt.gz -> foo.txt 且内容为原始文本; 嵌套 zip 内文件同样被解出;
        3. hint=sar 命中 sar:1.0; hint=netcap 命中 netcap:1.0; 无头 sar 文本以 0.6+0.25 判为 sar;
        4. unsupported[i]["hint"] 分别含 editcap 与 sadf;
        5. kind == "blktrace" 且 candidates == [];
        6. 6 个 sar 文件 -> 候选 4 个 + 上限 warning; 无关文本 -> kind 为 None。

    :AfterTest
        1. 全部为 tmp_path 下临时文件, pytest 自动清理, 无残留。
"""
import tarfile
import zipfile
from pathlib import Path

from app.modules.perf.detect import (MAX_GROUPS_PER_KIND, detect_kind, extract_for_perf)

SAR_TEXT = """Linux 5.10.0 (node-1) \t10/14/2026 \t_x86_64_\t(4 CPU)

12:00:01 AM     CPU     %user     %nice   %system   %iowait    %steal     %idle
12:00:11 AM     all      1.50      0.00      0.75      0.25      0.00     97.50
12:00:21 AM     all      3.00      0.00      1.00      2.00      0.00     94.00
Average:        all      2.25      0.00      0.88      1.13      0.00     95.75
"""

SAR_NO_HOST = """12:00:01 AM     CPU     %user     %nice   %system   %iowait    %steal     %idle
12:00:11 AM     all      1.50      0.00      0.75      0.25      0.00     97.50
"""

BLK_TEXT = """8,0    0        1     0.000000000   639  Q  WS 274432 + 8 [kworker/0:1]
8,0    0        5     0.000004000   639  D  WS 274432 + 8 [kworker/0:1]
8,0    0        6     0.001000000     0  C  WS 274432 + 8 [0]
"""

TSHARK_TEXT = """frame.time_epoch\tip.src\tip.dst\t_ws.col.protocol\tframe.len\ttcp.srcport
1.500000\t10.0.0.1\t10.0.0.2\tTCP\t74\t1234
"""

IRQ_TEXT = """            CPU0       CPU1
   0:         12          0   IO-APIC   2-edge      timer
   1:          3          4   IO-APIC   1-edge      i8042
LOC:    1234567    1234567   Local timer interrupts
"""

JUNK_TEXT = "this is a plain note\nnothing to analyze here\n"


def _write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def _tar_gz(archive: Path, entries: dict[str, str]) -> Path:
    src = archive.parent / "src"
    for name, text in entries.items():
        _write(src / name, text)
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname="perf")
    return archive


def test_extract_tar_gz(tmp_path: Path):
    archive = _tar_gz(tmp_path / "perf.tar.gz", {"sar.txt": SAR_TEXT, "note.txt": JUNK_TEXT})
    dest = tmp_path / "out"
    files, warnings = extract_for_perf(archive, dest)

    assert warnings == []
    assert sorted(f.name for f in files) == ["note.txt", "sar.txt"]
    result = detect_kind(files, "sar", base=dest)
    assert result.kind == "sar"
    assert result.scores["sar"] == 1.0
    assert [c.rel for c in result.candidates] == ["perf/sar.txt"]
    assert len(result.inventory) == 2


def test_extract_plain_gz_and_nested_zip(tmp_path: Path):
    import gzip
    archive = tmp_path / "bundle.zip"
    gz_path = tmp_path / "sar.txt.gz"
    with gzip.open(gz_path, "wb") as fp:
        fp.write(SAR_TEXT.encode("utf-8"))
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("blk.txt", BLK_TEXT)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(gz_path, "logs/sar.txt.gz")
        zf.write(inner, "logs/inner.zip")

    dest = tmp_path / "out"
    files, warnings = extract_for_perf(archive, dest)

    assert warnings == []
    names = sorted(f.name for f in files)
    assert names == ["blk.txt", "sar.txt"]
    gunzipped = next(f for f in files if f.name == "sar.txt")
    assert gunzipped.read_text(encoding="utf-8") == SAR_TEXT


def test_detect_by_tab_kind(tmp_path: Path):
    files = [
        _write(tmp_path / "sar.txt", SAR_TEXT),
        _write(tmp_path / "blk.txt", BLK_TEXT),
        _write(tmp_path / "cap.txt", TSHARK_TEXT),
        _write(tmp_path / "irq.txt", IRQ_TEXT),
    ]
    assert detect_kind(files, "sar", base=tmp_path).kind == "sar"
    assert detect_kind(files, "blktrace", base=tmp_path).scores["blktrace"] == 1.0
    assert detect_kind(files, "netcap", base=tmp_path).scores["netcap"] == 1.0
    assert detect_kind(files, "interrupts", base=tmp_path).scores["interrupts"] == 1.0


def test_detect_pcap_magic(tmp_path: Path):
    pcap = _write(tmp_path / "capture.pcap", b"\xd4\xc3\xb2\xa1" + b"\x00" * 20)
    result = detect_kind([pcap], "netcap", base=tmp_path)

    assert result.kind == "netcap"
    assert result.scores["netcap"] == 1.0
    assert result.unsupported == []


def test_weak_same_kind_wins(tmp_path: Path):
    # 无 Linux 头的 sar 文本: 弱证据 0.6 + 同类加成 0.25 >= 0.8 -> 判为 sar
    files = [_write(tmp_path / "cpu.txt", SAR_NO_HOST)]
    result = detect_kind(files, "sar", base=tmp_path)

    assert result.kind == "sar"
    assert result.scores["sar"] == 0.6
    assert len(result.candidates) == 1


def test_cross_kind_hint(tmp_path: Path):
    # 包内只有 blktrace 数据: sar 页签得到 kind=blktrace 且无候选 (前端提示切换页签)
    files = [_write(tmp_path / "blk.txt", BLK_TEXT)]
    result = detect_kind(files, "sar", base=tmp_path)

    assert result.kind == "blktrace"
    assert result.candidates == []


def test_unsupported_formats(tmp_path: Path):
    pcapng = _write(tmp_path / "t.pcapng", b"\x0a\x0d\x0d\x0a" + b"\x00" * 60)
    sa15 = _write(tmp_path / "sa15", b"\x00" * 128)
    hints = {(u["kind"], u["hint"].split()[0])
             for u in detect_kind([pcapng, sa15], "netcap", base=tmp_path).unsupported}

    assert ("netcap", "editcap") in hints
    assert ("sar", "sadf") in hints


def test_max_groups_and_not_found(tmp_path: Path):
    many = [_write(tmp_path / f"host{i}.txt", SAR_TEXT) for i in range(6)]
    result = detect_kind(many, "sar", base=tmp_path)

    assert len(result.candidates) == MAX_GROUPS_PER_KIND
    assert any("上限" in w for w in result.warnings)

    junk = [_write(tmp_path / "note.txt", JUNK_TEXT)]
    assert detect_kind(junk, "sar", base=tmp_path).kind is None
