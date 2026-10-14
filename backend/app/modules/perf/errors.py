"""性能分析解析器异常基类.

约定: 各解析器 (netcap / blktrace / sar ...) 的解析失败异常统一继承本基类,
API 层 (`app.api.perf`) 只需捕获 `PerfAnalysisError` 即可转 HTTP 400, 新增格式无需改 API。
"""


class PerfAnalysisError(ValueError):
    """性能日志解析失败基类, 由 API 层统一转 HTTP 400."""