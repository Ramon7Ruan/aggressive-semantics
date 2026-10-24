"""公共异常。

分类原则：调用方靠异常类型决定"该重试 / 该停 / 该改代码"，不靠字符串匹配。
"""


class DiscourseKitError(Exception):
    """所有本项目异常的基类。"""


class RobotsDisallow(DiscourseKitError):
    """robots 声明禁止访问该路径，或命中本项目的保守禁访前缀。

    出现这个异常意味着**不应该继续**，不是"重试一下"。调用方应记录并跳过该 URL。
    """


class TimeWindowBlocked(DiscourseKitError):
    """当前处于禁访时间窗（默认 00:00–07:00）。"""


class FetchError(DiscourseKitError):
    """请求失败且重试已耗尽，或遇到不可重试的状态码。"""


class TooManyBlocks(DiscourseKitError):
    """连续触发限流（429/403），疑似被风控。应立即停止，而不是继续试探。"""


class ParseError(DiscourseKitError):
    """页面结构解析失败。

    这是**硬失败**：解析出 0 条也必须抛错，不允许静默返回空列表——
    否则前端发版改类名时，采集会"成功"地写入 0 条数据。
    """


# ── 数据集导入与分析管线 ──────────────────────────────────────────


class SchemaError(DiscourseKitError):
    """一行数据不符合 schema v1。

    这是硬失败：宁可整批停下，也不要让结构错位的行流进指标层。
    """


class IngestError(DiscourseKitError):
    """导入失败：文件读不了、映射缺列、编码判断不出来。"""


class MappingError(IngestError):
    """字段映射不成立：映射里写了源文件没有的列，或必填字段没映射到。"""


class DataQualityError(DiscourseKitError):
    """质量闸门未通过（重复率过高、时间倒挂、文本为空等）。"""


class MetricUnavailable(DiscourseKitError):
    """指标依赖的字段或前提不满足。用于显式标注「这个指标算不出来」。"""
