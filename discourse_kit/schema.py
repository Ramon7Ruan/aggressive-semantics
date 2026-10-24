"""schema v1：整个项目的唯一数据契约。

设计原则
--------
1. **只有四个必填字段**，其余全部可选。理由：公开数据集的字段完整度无法预设，
   如果 schema 要求 20 个字段，任何数据集都进不来。
2. **可选字段缺席不等于错误**。指标自己声明依赖哪些字段，缺了就跳过并记账，
   而不是让整条管线崩掉或悄悄产出 NaN。
3. 一个「分析单元」= 一行。`unit` 取值见 UNIT_*，这是区分主楼/回复/论据的唯一依据。

字段命名与含义一旦发布不得随意改动，因为下游指标、导出、报告都绑在这里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from .errors import SchemaError

# ── 分析单元 ──────────────────────────────────────────────────────
UNIT_OPENING = "opening"      # 主楼 / 辩题陈述 / 话题发起
UNIT_REPLY = "reply"          # 楼层回复 / 评论
UNIT_ARGUMENT = "argument"    # 结构化论点或论据（部分数据集是这一层）
UNITS = (UNIT_OPENING, UNIT_REPLY, UNIT_ARGUMENT)

# ── 字段契约 ──────────────────────────────────────────────────────
REQUIRED: tuple[str, ...] = ("doc_id", "thread_id", "unit", "text")

#: 可选字段 → 说明。顺序即导出列顺序。
OPTIONAL: dict[str, str] = {
    "parent_id": "被回复单元的 doc_id；顶层为空",
    "platform": "来源平台，如 douban / weibo / zhihu / hupu",
    "board": "版块 / 小组名",
    "topic_id": "数据集内的话题编号，用于整群抽样",
    "topic_title": "话题标题",
    "floor_no": "楼层序号，从 1 起",
    "author_id": "已哈希的作者标识",
    "author_level": "平台等级（若提供）",
    "posted_at": "发布时刻，ISO 字符串；缺失时为 None",
    "is_op": "是否楼主/发起者",
    "has_quote": "是否包含引用",
    "quote_text": "引用原文",
    "is_featured": "平台精选标记",
    "reply_count": "该帖回复总数",
    "view_count": "浏览量",
    "ip_region": "IP 属地（省级）",
    "stance": "数据集自带立场标签：pro / con / neutral",
    "argument_type": "数据集自带论据类型标签",
    "credibility": "数据集自带可信度等级",
    "source": "数据来源标识，如 nbsdc-8sBYreNN / synthetic",
}

ALL_FIELDS: tuple[str, ...] = REQUIRED + tuple(OPTIONAL)

#: 时间相关字段。缺了就没法算任何「随时间演变」的指标。
TIME_FIELDS = ("posted_at",)


@dataclass(slots=True)
class Record:
    """一条分析单元。用 slots 是因为几十万行时普通 dataclass 内存翻倍。"""

    doc_id: str
    thread_id: str
    unit: str
    text: str
    parent_id: str | None = None
    platform: str | None = None
    board: str | None = None
    topic_id: str | None = None
    topic_title: str | None = None
    floor_no: int | None = None
    author_id: str | None = None
    author_level: int | None = None
    posted_at: str | None = None
    is_op: bool = False
    has_quote: bool = False
    quote_text: str | None = None
    is_featured: bool = False
    reply_count: int | None = None
    view_count: int | None = None
    ip_region: str | None = None
    stance: str | None = None
    argument_type: str | None = None
    credibility: str | None = None
    source: str | None = None

    # 运行时附加（不入库、不导出）：由 normalize 补齐
    created_at: datetime | None = field(default=None, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in ALL_FIELDS}

    def have(self, name: str) -> bool:
        """该字段是否有实际取值（非 None、非空串）。"""
        v = getattr(self, name, None)
        return v is not None and v != "" and v != []


def _as_int(v: Any, name: str) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip().replace(",", "")
    # 允许 "1.2万" / "3w" 这类平台写法
    mult = 1
    if s.endswith(("万", "w", "W")):
        mult, s = 10_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError as exc:
        raise SchemaError(f"字段 {name} 期望整数，得到 {v!r}") from exc


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "y", "t", "是", "有", "1.0"}


def coerce(row: Mapping[str, Any]) -> Record:
    """把任意来源的一行字典转成 Record。

    只做类型转换与必填校验，**不做内容清洗**（那是 normalize 的事）。
    未知键直接丢弃，避免数据集的多余列污染下游。
    """
    missing = [f for f in REQUIRED if row.get(f) in (None, "")]
    if missing:
        raise SchemaError(f"缺少必填字段 {missing}；行内容前 120 字：{str(row)[:120]}")

    unit = str(row["unit"]).strip()
    if unit not in UNITS:
        raise SchemaError(f"unit 必须是 {UNITS} 之一，得到 {unit!r}")

    kwargs: dict[str, Any] = {
        "doc_id": str(row["doc_id"]).strip(),
        "thread_id": str(row["thread_id"]).strip(),
        "unit": unit,
        "text": str(row["text"]),
    }
    for name in OPTIONAL:
        v = row.get(name)
        if name in ("floor_no", "author_level", "reply_count", "view_count"):
            kwargs[name] = _as_int(v, name)
        elif name in ("is_op", "has_quote", "is_featured"):
            kwargs[name] = _as_bool(v)
        elif name == "posted_at":
            kwargs[name] = str(v).strip() if v not in (None, "") else None
        else:
            kwargs[name] = (str(v).strip() if v not in (None, "") else None)
    return Record(**kwargs)


def coerce_all(rows: Iterable[Mapping[str, Any]]) -> list[Record]:
    return [coerce(r) for r in rows]


def capabilities(rows: Iterable[Record]) -> dict[str, float]:
    """统计每个可选字段的覆盖率（0–1）。这是判断「能算什么指标」的唯一依据。"""
    rows = list(rows)
    n = len(rows)
    if not n:
        return {f: 0.0 for f in OPTIONAL}
    return {f: sum(1 for r in rows if r.have(f)) / n for f in OPTIONAL}


def available_fields(rows: Iterable[Record], threshold: float = 0.0) -> set[str]:
    """覆盖率高于阈值的字段集合。threshold=0 表示「至少有一个非空」。"""
    caps = capabilities(rows)
    return {f for f, c in caps.items() if c > threshold}


def missing_for(rows: Iterable[Record], fields: Iterable[str]) -> list[str]:
    """给定需求字段，返回其中尚无任何取值的那些。"""
    rows = list(rows)
    return [f for f in fields if not any(r.have(f) for r in rows)]
