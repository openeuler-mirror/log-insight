"""性能分析: 中断计数变化解析器 (app.modules.perf.interrupts) 单元测试

    :Preparation
        1. 无需外部基础设施, 直接调用纯函数 analyze_interrupts(data, filename), 不触碰 DB/Redis;
        2. 准备三份样本:
           a) /proc/interrupts 双快照(4 CPU, 含 timer/MSI 数值 IRQ 与 LOC/RES/CAL/TLB/NMI 命名 IRQ);
           b) /proc/interrupts 单快照(仅累计计数, 无法算变化);
           c) sar -I SUM -P ALL 文本(含 all 汇总行与 Average 行);
           d) sar -I ALL 文本(单列 CPU=all, 多中断名);
           e) /proc/interrupts 双快照但第二份存在计数回退(IRQ 重分配)。

    :TestStep
        1. 解析 a, 校验格式识别、快照数、CPU 数与各中断源变化量;
        2. 校验 per-CPU 变化量合计等于总量, Top 排序与占比;
        3. 解析 b, 校验变化区间为 0 且带"仅检测到 1 个快照"告警;
        4. 解析 c, 校验单位=每秒、采样间隔=10s、Average 行被跳过、均值与峰值;
        5. 解析 d, 校验按中断名拆分、Top 为 LOC;
        6. 解析 e, 校验负差值按 0 处理并置位回退告警;
        7. 反向用例: 空文件 / 无法识别文本 均应抛 InterruptError。

    :ExpectResult
        1. format=proc-interrupts, unit=count, cpu_count=4, sample_count=2, interval_count=1;
        2. 变化总量 4562, CPU0..CPU3 分别为 1194/1148/1121/1099 且求和相等, Top[0]=LOC(4000);
        3. interval_count=0, warnings 含"仅检测到 1 个快照", Top[0].value=4000000;
        4. format=sar-irq, unit=per_s, interval_s=10.0, avg=1250.0, peak.value=1500.0, CPU0=400.0;
        5. top[0].name=LOC 且 value=3728.0;
        6. 该中断源变化量为 0, warnings 含"计数回退";
        7. 均抛 InterruptError, 且是 PerfAnalysisError 子类, 消息可读。

    :AfterTest
        1. 全部为内存内纯函数调用, 不产生临时文件与数据库记录, 无残留。
"""
import pytest

from app.modules.perf.errors import PerfAnalysisError
from app.modules.perf.interrupts import InterruptError, analyze_interrupts

SNAP1 = """            CPU0       CPU1       CPU2       CPU3
   0:         12          0          0          0   IO-APIC   2-edge      timer
  16:        100         90         80         70   PCI-MSI 524288-edge  nvme0q0
 LOC:    1000000    1000000    1000000    1000000   Local timer interrupts
 RES:       5000       4000       3000       2000   Rescheduling interrupts
 CAL:       2000       2000       2000       2000   Function call interrupts
 TLB:        500        400        300        200   TLB shootdowns
 NMI:         10         10         10         10   Non-maskable interrupts
"""

SNAP2 = """            CPU0       CPU1       CPU2       CPU3
   0:         16          0          0          0   IO-APIC   2-edge      timer
  16:        130        100         85         75   PCI-MSI 524288-edge  nvme0q0
 LOC:    1001000    1001000    1001000    1001000   Local timer interrupts
 RES:       5100       4080       3060       2040   Rescheduling interrupts
 CAL:       2050       2050       2050       2050   Function call interrupts
 TLB:        510        408        306        204   TLB shootdowns
 NMI:         10         10         10         10   Non-maskable interrupts
"""

PROC_TWO = SNAP1 + "\n" + SNAP2

SAR_SUM = """Linux 5.10.0-60.18.0.50.oe2203.aarch64 (node-1) \t10/14/2026 \t_aarch64_\t(4 CPU)

10:12:01 AM     CPU     intr/s
10:12:11 AM     all   1000.00
10:12:11 AM       0    300.00
10:12:11 AM       1    250.00
10:12:11 AM       2    250.00
10:12:11 AM       3    200.00
10:12:21 AM     all   1500.00
10:12:21 AM       0    500.00
10:12:21 AM       1    400.00
10:12:21 AM       2    350.00
10:12:21 AM       3    250.00

Average:        CPU     intr/s
Average:        all   1250.00
"""

SAR_ALL = """10:30:01 AM     CPU     INTR     intr/s
10:30:11 AM     all      LOC    3456.00
10:30:11 AM     all      RES    1200.00
10:30:11 AM     all      TLB     300.00
10:30:21 AM     all      LOC    4000.00
10:30:21 AM     all      RES    1000.00
10:30:21 AM     all      TLB     250.00
"""

# 第二份快照中 16 号 IRQ 各列计数均变小(IRQ 被释放/重分配), 差值应全部钳为 0
SNAP_RESET = SNAP1.replace(
    "  16:        100         90         80         70   PCI-MSI 524288-edge  nvme0q0",
    "  16:         20         10          5          5   PCI-MSI 524288-edge  nvme0q0",
)


def _proc_two() -> dict:
    return analyze_interrupts(PROC_TWO.encode("utf-8"), "interrupts.txt")


def test_proc_two_snapshots():
    body = _proc_two()
    assert body["format"] == "proc-interrupts"
    assert body["unit"] == "count"
    assert body["metric_label"] == "变化量"
    assert body["cpu_count"] == 4
    assert body["sample_count"] == 2
    assert body["interval_count"] == 1
    assert body["total"] == 4562.0
    assert len(body["series"]["x"]) == 1
    assert body["series"]["x"] == ["#1"]


def test_proc_per_cpu_and_top():
    body = _proc_two()
    per_cpu = [c["value"] for c in body["per_cpu"]]
    assert per_cpu == [1194.0, 1148.0, 1121.0, 1099.0]
    assert sum(per_cpu) == body["total"]
    assert body["per_cpu"][0]["cpu"] == "CPU0"
    assert body["per_cpu"][0]["last_total"] == 1008816
    top0 = body["top"][0]
    assert top0["name"] == "LOC"
    assert top0["value"] == 4000.0
    assert top0["desc"] == "Local timer interrupts"
    assert body["top"][1]["name"] == "RES"
    assert top0["share"] == pytest.approx(87.68, abs=0.01)


def test_proc_single_snapshot():
    body = analyze_interrupts(SNAP1.encode("utf-8"))
    assert body["interval_count"] == 0
    assert body["series"]["x"] == []
    assert any("仅检测到 1 个快照" in w for w in body["warnings"])
    assert body["top"][0]["name"] == "LOC"
    assert body["top"][0]["value"] == 4000000.0


def test_proc_counter_reset_clamped():
    body = analyze_interrupts((SNAP1 + "\n" + SNAP_RESET).encode("utf-8"))
    irq16 = next(t for t in body["top"] if t["name"] == "16")
    assert irq16["value"] == 0.0
    assert any("计数回退" in w for w in body["warnings"])


def test_sar_sum():
    body = analyze_interrupts(SAR_SUM.encode("utf-8"), "sar.txt")
    assert body["format"] == "sar-irq"
    assert body["unit"] == "per_s"
    assert body["interval_s"] == 10.0
    assert body["sample_count"] == 2
    assert body["cpu_count"] == 4
    assert body["avg"] == 1250.0                       # Average 行不入序列
    assert body["peak"]["value"] == 1500.0
    assert body["peak"]["at"] == "10:12:21"
    assert [c["value"] for c in body["per_cpu"]] == [400.0, 325.0, 300.0, 225.0]
    assert body["top"][0]["name"] == "SUM"


def test_sar_all_breakdown():
    body = analyze_interrupts(SAR_ALL.encode("utf-8"))
    assert body["format"] == "sar-irq"
    assert body["top"][0]["name"] == "LOC"
    assert body["top"][0]["value"] == 3728.0
    assert body["avg"] == 5103.0
    assert body["series"]["x"] == ["10:30:11", "10:30:21"]


def test_errors_are_friendly():
    assert issubclass(InterruptError, PerfAnalysisError)
    with pytest.raises(InterruptError, match="文件为空"):
        analyze_interrupts(b"")
    with pytest.raises(InterruptError, match="未识别"):
        analyze_interrupts(b"hello world\nno interrupt data here\n")
