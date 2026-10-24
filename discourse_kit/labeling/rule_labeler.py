"""规则标注器：给话题类型一个可复现的**先验**。

明确它不是什么：**这不是最终标签。**
规则标注的用途只有两个：
1. 分层抽样时给出初始分层（否则无法保证 T1–T5 都有样本）；
2. 给 LLM 标注做对照，用来发现两者系统性分歧的位置。

如果把它当最终标签，研究就变成「关键词分类 + 指标计算」，
κ 无从估计，结论无法辩护。所以代码里强制要求与人工金标准比对后才能使用，
`agreement.py` 提供这个比对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..normalize import char_count
from ..schema import UNIT_OPENING, Record
from .codebook import UNKNOWN

# 指向性与判断类型信号。判定顺序见 _decide()，顺序本身就是研究设计的一部分：
# 「指向第三方」必须最先判，否则「他在某市」这类文本会被后面所有线索抢走。
_PAT = {}

# 指向性与判断类型信号。顺序即判定优先级，见 classify()。
_WHY = re.compile(r"为什么|为何|什么原因|成因|如何解释|怎么会|什么导致|背后的原因|是怎么形成")
_HELP = re.compile(r"请问|求助|怎么办|该注意|注意哪些|求建议|有没有人|怎么处理|如何办理"
                   r"|求推荐|想问|请教|有没有影响|会有影响吗|靠谱吗")
_DECIDE = re.compile(r"该不该|应不应该|是否应该|要不要|值不值得|该不该禁止")
_EVAL = re.compile(r"如何看待|怎么看待|如何评价|怎么评价|支持还是反对|算不算|合不合理")
_SELF = re.compile(r"我(?:该|要|是不是|想|遇到|纠结|的|这)|自己|本人")
_THIRD = re.compile(r"这人|此人|这种人|他|她|某地|某省|某市|主播|明星|老板|领导|"
                    r"同事|室友|邻居|老师|医生|专家|网红|选手|球员|博主|up主")
_ABSTRACT = re.compile(r"社会|政策|制度|现象|行业|国家|城市|教育|医疗|房价|就业|法律"
                       r"|趋势|规定|学校|单位|平台|市场|政府")
_GROUP = re.compile(r"都这么|都这样|这类人|这种人|地方的人|一代人|群体|所有人|普遍")


@dataclass
class RuleLabel:
    code: str
    score: float
    hits: dict[str, int]


def _decide(t: str) -> str:
    """单段文本的判定树。返回 T1–T5 或 T?。"""
    if not t:
        return UNKNOWN
    # 1 指向不在场的第三方 → T5（先判，因为「他在某地」这类文本同时命中其他线索）
    if _THIRD.search(t) and not _SELF.search(t):
        return "T5"
    # 2 落点是自己 + 两难选择 → T4
    if _SELF.search(t) and (_DECIDE.search(t) or "要不要" in t):
        return "T4"
    # 3 落点是抽象对象 + 规范判断 → T3
    if (_DECIDE.search(t) or _EVAL.search(t)) and _ABSTRACT.search(t):
        return "T3"
    # 4 询问成因 → T1
    if _WHY.search(t):
        return "T1"
    # 5 求具体建议 → T2
    if _HELP.search(t):
        return "T2"
    # 6 兜底：规范判断但对象不明，按抽象议题处理
    if _DECIDE.search(t) or _EVAL.search(t):
        return "T3"
    return UNKNOWN


_PAT = {"T1": _WHY, "T2": _HELP, "T3": _EVAL, "T4": _DECIDE, "T5": _THIRD}


def classify(text: str) -> RuleLabel:
    """对一段文本（通常是标题）做判定，并记录命中的线索类型数。"""
    t = (text or "").strip()
    if not t:
        return RuleLabel(UNKNOWN, 0.0, {})
    code = _decide(t)
    hits = {c: len(p.findall(t)) for c, p in _PAT.items() if p.search(t)}
    return RuleLabel(code, float(sum(hits.values())), hits)


def classify_titled(title: str | None, body: str) -> RuleLabel:
    """标题优先；标题判不出时用正文前 200 字。"""
    first = classify(title or "")
    if first.code != UNKNOWN:
        return first
    return classify(f"{title or ''} {body[:200]}")


def subtype(code: str, text: str) -> str | None:
    """T5 的三分。仅当 code == T5 时有意义。"""
    if code != "T5":
        return None
    if re.search(r"这人|此人|他|她|某[个位名]", text):
        return "T5a"
    if re.search(r"都这样|这类人|这种人|地方的人|群体|一代人", text):
        return "T5b"
    return "T5c"


def label_openings(records: list[Record]) -> list[tuple[str, str, str, float, str]]:
    """给主楼打话题类型。返回 (doc_id, dimension, label, score, method) 行。"""
    rows = []
    for r in records:
        if r.unit != UNIT_OPENING:
            continue
        lab = classify_titled(r.topic_title, r.text)
        rows.append((r.doc_id, "topic_type", lab.code, lab.score, "rule"))
        st = subtype(lab.code, f"{r.topic_title or ''} {r.text[:200]}")
        if st:
            rows.append((r.doc_id, "topic_subtype", st, lab.score, "rule"))
    return rows


def thread_topic_map(records: list[Record]) -> dict[str, str]:
    """thread_id → 话题类型（取该帖主楼的规则标签）。"""
    out: dict[str, str] = {}
    for r in records:
        if r.unit == UNIT_OPENING:
            out[r.thread_id] = classify_titled(r.topic_title, r.text).code
    return out


def coverage_warning(records: list[Record], *, min_chars: int = 8) -> list[str]:
    """检查主楼是否短到无法编码。返回告警列表。"""
    warns = []
    for r in records:
        if r.unit == UNIT_OPENING and char_count(r.text) < min_chars:
            warns.append(f"{r.doc_id}: 主楼仅 {char_count(r.text)} 字，编码可信度低")
    return warns
