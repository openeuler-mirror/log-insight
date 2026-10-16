"""故障诊断: 通用文本解析器时间戳解析与回填测试

    :Preparation
        1. 无需外部基础设施, 直接调用解析器模块 (app.modules.diagnosis.parsers);
        2. 准备四类时间样本: ISO 空格分隔 / ISO 毫秒+时区偏移 / syslog 无年份 / 非法文本;
        3. 准备一个临时日志文件(tmp_path), 含一行带时间戳与一行无时间戳。

    :TestStep
        1. parse_timestamp 解析 "2026-10-10 12:00:00";
        2. parse_timestamp 解析 "2026-10-10T12:00:25.123+08:00";
        3. parse_timestamp 解析 "Oct 10 12:00:30" (syslog, 无年份);
        4. parse_timestamp 解析非法文本与空串;
        5. GenericTextParser 解析临时日志文件。

    :ExpectResult
        1. 得到 datetime(2026, 10, 10, 12, 0, 0);
        2. 非空且带 tzinfo, 时分秒为 12:00:25;
        3. 月日时分秒为 10/10 12:00:30, 年份为当前年;
        4. 均返回 None;
        5. 第 1 条 entry.timestamp 回填为 2026-10-10 12:00:00, 无时间戳行保持 None。

    :AfterTest
        1. 临时文件由 pytest 的 tmp_path 自动清理;
        2. 不触碰 DB/Redis, 无数据残留。
"""
from datetime import datetime

from app.modules.diagnosis.parsers import GenericTextParser, parse_timestamp


def test_iso_space_separator():
    assert parse_timestamp("2026-10-10 12:00:00") == datetime(2026, 10, 10, 12, 0, 0)


def test_iso_millis_with_offset():
    got = parse_timestamp("2026-10-10T12:00:25.123+08:00")
    assert got is not None
    assert got.utcoffset() is not None
    assert (got.hour, got.minute, got.second) == (12, 0, 25)


def test_syslog_uses_current_year():
    got = parse_timestamp("Oct 10 12:00:30")
    assert got is not None
    assert (got.month, got.day, got.hour, got.minute, got.second) == (10, 10, 12, 0, 30)
    assert got.year == datetime.now().year


def test_invalid_returns_none():
    assert parse_timestamp("no timestamp here") is None
    assert parse_timestamp("") is None


def test_generic_parser_backfills_timestamp(tmp_path):
    log = tmp_path / "syslog.log"
    log.write_text("2026-10-10 12:00:00 kernel: Out of memory\n"
                   "plain line without ts\n", encoding="utf-8")
    result = GenericTextParser().parse_file(log)
    assert result.entries[0].timestamp == datetime(2026, 10, 10, 12, 0, 0)
    assert result.entries[1].timestamp is None