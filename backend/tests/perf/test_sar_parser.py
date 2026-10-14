"""性能分析: sar 文本解析器 (app.modules.perf.sar) 单元测试

    :Preparation
        1. 无需外部基础设施, 直接调用纯函数 analyze_sar(data, filename), 不触碰 DB/Redis;
        2. 准备一份 LC_ALL=C 的 sar 文本: 含 CPU(-P ALL, 含 per-CPU 与 Average 行)、
           内存(-r)、磁盘(-d, 旧版 rd_sec/s/wr_sec/s 列) 三个数据段, 以及一行无法识别的表头;
        3. 另备: 新版磁盘列(rkB/s/wkB/s/areq-sz/aqu-sz)、12 小时制 PM 时间、超长序列三份样本。

    :TestStep
        1. 解析主样本, 校验元信息(host/kernel/date/cpu_count)与 views 段列表;
        2. 校验 CPU 段: Average 行被跳过(points_total=2)、all 汇总均值、峰值、per-CPU 摘要;
        3. 校验内存段: 平均/峰值使用率与反推的总量;
        4. 校验磁盘段: rd_sec/s 按 ÷2 换算为 KB/s、await/%util 取值正确、设备汇总排序;
        5. 解析新版磁盘列样本, 校验 rkB/s 不换算且别名列(areq-sz/aqu-sz)可解析;
        6. 解析 PM 样本, 校验 12 小时制归一化为 24 小时制;
        7. 解析超长 CPU 样本, 校验抽稀后点数 <= POINTS_MAX 且 thinned=True;
        8. 反向用例: 空文件 / 二进制 / 无任何可识别段 均应抛出 SarError。

    :ExpectResult
        1. format=sar, host=node-1, cpu_count=4, views=[cpu, mem, disk];
        2. Average 行不入点; CPU busy=4.25; per-CPU 按 busy 降序; 含 per-CPU 提示 warning;
        3. 内存平均使用率 76.25%, 峰值 77.5%, 总量 4000000 KB;
        4. dev8-0 读 75.0 / 写 150.0 KB/s, await 32.5ms, 最大 %util 85.0;
        5. 读 1000.0 / 写 2000.0 KB/s (未除以 2), avgqu 0.1, await 5.0, %util 10.0;
        6. 时钟归一化为 12:59:59 与 13:00:00;
        7. points_total=2100, thinned=True, len(points) <= 2000;
        8. 均抛 SarError, 且 SarError 是 PerfAnalysisError 的子类, 消息可读。

    :AfterTest
        1. 全部为内存内纯函数调用, 不产生临时文件与数据库记录, 无残留。
"""
import pytest

from app.modules.perf.errors import PerfAnalysisError
from app.modules.perf.sar import POINTS_MAX, SarError, analyze_sar

SAMPLE_MAIN = """Linux 5.10.0-60.18.0.50.oe2203.aarch64 (node-1) \t10/14/2026 \t_aarch64_\t(4 CPU)

12:00:01 AM     CPU     %user     %nice   %system   %iowait    %steal     %idle
12:00:11 AM     all      1.50      0.00      0.75      0.25      0.00     97.50
12:00:11 AM       0      1.00      0.00      0.50      0.10      0.00     98.40
12:00:11 AM       1      2.00      0.00      1.00      0.40      0.00     96.60
12:00:21 AM     all      3.00      0.00      1.00      2.00      0.00     94.00
Average:        all      2.25      0.00      0.88      1.13      0.00     95.75

12:00:01 AM kbmemfree kbavail kbmemused  %memused kbbuffers  kbcached
12:00:11 AM   1000000 1200000   3000000     75.00    100000    500000
12:00:21 AM    900000 1100000   3100000     77.50    100000    500000
Average:      950000 1150000   3050000     76.25    100000    500000

12:00:01 AM       DEV       tps  rd_sec/s  wr_sec/s  avgrq-sz  avgqu-sz     await     svctm     %util
12:00:11 AM    dev8-0      1.00    100.00    200.00     10.00      0.10      5.00      1.00     10.00
12:00:11 AM    dev8-1      2.00     50.00    100.00     20.00      0.20     12.00      2.00     20.00
12:00:21 AM    dev8-0      3.00    200.00    400.00     30.00      0.30     60.00      3.00     85.00
Average:       dev8-0      2.00    150.00    300.00     20.00      0.20     32.50      2.00     47.50

12:00:31 AM   %idle   bogus
"""

SAMPLE_RKB = """Linux 5.10.0-60.18.0.50.oe2203.aarch64 (node-1) \t10/14/2026 \t_aarch64_\t(4 CPU)

12:00:01 AM       DEV       tps   rkB/s   wkB/s  areq-sz   aqu-sz     await     svctm     %util
12:00:11 AM    dev8-0      1.00 1000.00 2000.00    10.00     0.10      5.00      1.00     10.00
"""

SAMPLE_PM = """Linux 5.10.0-60.18.0.50.oe2203.aarch64 (node-2) \t10/14/2026 \t_aarch64_\t(2 CPU)

12:00:00 PM     CPU     %user     %idle
12:59:59 PM     all     10.00     90.00
01:00:00 PM     all     20.00     80.00
"""


def _big_cpu_sample(n: int) -> str:
    lines = ["12:00:00 AM     CPU     %user     %idle"]
    for i in range(n):
        h, rem = divmod(i, 3600)
        mi, se = divmod(rem, 60)
        lines.append(f"{h:02d}:{mi:02d}:{se:02d} AM     all     10.00     90.00")
    return "\n".join(lines) + "\n"


def _dev(devices: list[dict], name: str) -> dict:
    return next(d for d in devices if d["dev"] == name)


def test_meta_and_views():
    out = analyze_sar(SAMPLE_MAIN.encode("utf-8"), "sar.txt")
    assert out["format"] == "sar"
    assert out["host"] == "node-1"
    assert out["kernel"] == "5.10.0-60.18.0.50.oe2203.aarch64"
    assert out["date"] == "10/14/2026"
    assert out["cpu_count"] == 4
    assert out["views"] == ["cpu", "mem", "disk"]
    assert out["interval_s"] == 10.0
    assert out["start_clock"] == "00:00:11"
    assert out["end_clock"] == "00:00:21"


def test_cpu_aggregate_skips_average_and_per_cpu():
    cpu = analyze_sar(SAMPLE_MAIN.encode("utf-8"))["cpu"]
    assert cpu["points_total"] == 2          # Average 行被跳过
    assert cpu["avg"]["user"] == 2.25
    assert cpu["avg"]["idle"] == 95.75
    assert cpu["avg"]["busy"] == 4.25
    assert cpu["peak"]["busy"] == 6.0        # 100 - 94.00
    names = [p["cpu"] for p in cpu["per_cpu"]]
    assert names == ["1", "0"]               # 按 busy 降序 (3.40 > 1.60)
    assert cpu["per_cpu"][0]["avg_busy"] == 3.4
    assert cpu["points"][0]["clock"] == "00:00:11"


def test_mem_aggregate():
    mem = analyze_sar(SAMPLE_MAIN.encode("utf-8"))["mem"]
    assert mem["avg"]["mem_used_pct"] == 76.25
    assert mem["peak"]["mem_used_pct"] == 77.5
    assert mem["mem_total_kb"] == 4000000     # 3000000 / 0.75 反推
    assert mem["points_total"] == 2


def test_disk_sector_to_kbps_and_util():
    disk = analyze_sar(SAMPLE_MAIN.encode("utf-8"))["disk"]
    assert disk["device_total"] == 2
    dev0 = _dev(disk["devices"], "dev8-0")
    assert dev0["avg_read_kbps"] == 75.0      # rd_sec/s 100/200 -> 50/100 取均值
    assert dev0["avg_write_kbps"] == 150.0
    assert dev0["avg_await_ms"] == 32.5
    assert dev0["max_await_ms"] == 60.0
    assert dev0["max_util_pct"] == 85.0
    assert disk["devices"][0]["dev"] == "dev8-0"   # 按 max_util 降序
    assert disk["totals"]["max_util_pct"] == 85.0
    assert disk["totals"]["max_util_dev"] == "dev8-0"


def test_disk_rkb_columns_without_conversion():
    disk = analyze_sar(SAMPLE_RKB.encode("utf-8"))["disk"]
    dev0 = _dev(disk["devices"], "dev8-0")
    assert dev0["avg_read_kbps"] == 1000.0    # rkB/s 原值
    assert dev0["avg_write_kbps"] == 2000.0
    assert dev0["avg_await_ms"] == 5.0
    assert dev0["avg_util_pct"] == 10.0


def test_pm_clock_normalized_to_24h():
    cpu = analyze_sar(SAMPLE_PM.encode("utf-8"))["cpu"]
    clocks = [p["clock"] for p in cpu["points"]]
    assert clocks == ["12:59:59", "13:00:00"]


def test_long_series_is_thinned():
    cpu = analyze_sar(_big_cpu_sample(POINTS_MAX + 100).encode("utf-8"))["cpu"]
    assert cpu["points_total"] == POINTS_MAX + 100
    assert cpu["thinned"] is True
    assert len(cpu["points"]) <= POINTS_MAX


def test_unrecognized_header_produces_warning():
    warnings = analyze_sar(SAMPLE_MAIN.encode("utf-8"))["warnings"]
    assert any("未识别" in w for w in warnings)
    assert any("per-CPU" in w for w in warnings)


@pytest.mark.parametrize("payload", [b"", b"\x00\x01\x02\x03binary-data"])
def test_invalid_input_raises_sar_error(payload):
    with pytest.raises(SarError):
        analyze_sar(payload)


def test_text_without_section_raises():
    with pytest.raises(SarError) as ei:
        analyze_sar(b"no sar section here\nanother plain line\n")
    assert "未识别" in str(ei.value)


def test_sar_error_is_perf_analysis_error():
    assert issubclass(SarError, PerfAnalysisError)