"""性能分析模块: 抓包 (pcap/tshark 导出) 与 blktrace (blkparse 文本) 的离线解析统计.

设计约定:
- 纯 Python 实现, 不引入 tshark/dpkt 等外部依赖, 便于跨平台部署
- 每个解析器统一签名 analyze(data: bytes, ...) -> dict, 返回前端可直接渲染的聚合结果
- 解析失败以 exceptions 抛出友好信息, 由 API 层转 400
"""
from app.modules.perf.blktrace import analyze_blktrace
from app.modules.perf.netcap import analyze_netcap

__all__ = ["analyze_netcap", "analyze_blktrace"]