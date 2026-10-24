"""人工话题编码表：把「给定分组」变成管线里的 topic_type。

为什么需要单独一层：
这份语料没有主楼，规则标注器只能看到标题，而标题常是省略句
（「外国人」「文科生」「红黄蓝」）。用规则去猜，等于把噪声当自变量。

所以话题类型改为**给定分组**：由人读完全部 16 个话题后定义，
存进 YAML，管线只负责读取与校验。这不是回避编码，恰恰是编码
应有的做法——编码方案与编码结果都要可审计。

校验规则（任何一条不满足都抛错，不静默降级）：
1. 每个 code 必须是已定义的类别；
2. 语料里出现的每个话题必须在表里有编码（漏一个就会让该话题整组消失）；
3. 表里的话题若语料中没有，只警告（用于交换数据集时的容错）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

UNKNOWN = "T?"

# 类别全集。缩窄后的研究只比较 T3 与 T5，但保留全集，
# 以便将来换平台时 T1/T2/T4 可直接启用。
CODES: dict[str, str] = {
    "T1": "解释型（共识 × 社会现象）",
    "T2": "求助型（共识 × 自身处境）",
    "T3": "制度指向（争议 × 政策/制度/做法）",
    "T4": "抉择型（争议 × 自身处境）",
    "T5": "行动者指向（争议 × 不在场第三方）",
}

SUBTYPES: dict[str, str] = {
    "person": "个人审判",
    "group": "群体审判",
    "org": "机构/企业",
    "nation": "国家",
    "policy": "政策/制度",
    "phenomenon": "社会现象",
}


@dataclass
class TopicCoding:
    topic_id: str
    thread_id: str
    code: str
    subtype: str = ""
    confidence: str = ""
    focus: str = ""
    rationale: str = ""


@dataclass
class TopicTable:
    path: str = ""
    thread_prefix: str = ""
    topics: dict[str, TopicCoding] = field(default_factory=dict)

    # ── 派生 ────────────────────────────────────────────────────
    @property
    def codes(self) -> dict[str, str]:
        """thread_id → code。管线直接用这个。"""
        return {t.thread_id: t.code for t in self.topics.values()}

    def by_code(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self.topics.values():
            out.setdefault(t.code, []).append(t.topic_id)
        return out

    def label_rows(self) -> list[tuple[str, str, str, float, str]]:
        """转成 store 需要的 (doc_id, dimension, label, score, method) 行。

        doc_id 用 thread_id：话题类型是**帖级属性**，挂在该话题的 thread 上。
        """
        return [(t.thread_id, "topic_type", t.code, 1.0, "manual")
                for t in self.topics.values()]

    def subtype_rows(self) -> list[tuple[str, str, str, float, str]]:
        return [(t.thread_id, "topic_subtype", t.subtype, 1.0, "manual")
                for t in self.topics.values() if t.subtype]


def load(path: str | Path) -> TopicTable:
    """读取并校验编码表。"""
    import yaml

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"话题编码表不存在：{p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    prefix = str(raw.get("thread_prefix", "") or "")
    items = raw.get("topics") or {}
    if not items:
        raise ValueError(f"{p} 里没有 topics 段")

    table = TopicTable(path=str(p), thread_prefix=prefix)
    bad: list[str] = []
    for key, spec in items.items():
        spec = spec or {}
        code = str(spec.get("code", "") or "").strip()
        if code not in CODES:
            bad.append(f"{key} → {code!r}")
            continue
        subtype = str(spec.get("subtype", "") or "")
        if subtype and subtype not in SUBTYPES:
            bad.append(f"{key} 的 subtype {subtype!r} 未定义")
        table.topics[str(key)] = TopicCoding(
            topic_id=str(key),
            thread_id=f"{prefix}{key}",
            code=code,
            subtype=subtype,
            confidence=str(spec.get("confidence", "") or ""),
            focus=str(spec.get("focus", "") or ""),
            rationale=str(spec.get("rationale", "") or ""),
        )
    if bad:
        raise ValueError("话题编码表存在无效取值：" + "；".join(bad))
    return table


def check_coverage(table: TopicTable, thread_ids) -> tuple[list[str], list[str]]:
    """返回 (语料中未编码的话题, 表中多余的编码)。

    第一项非空时必须让管线失败——漏编码的话题会整组从比较中消失，
    而这件事不会在结果里留下任何痕迹。
    """
    present = set(thread_ids)
    coded = set(table.codes)
    return sorted(present - coded), sorted(coded - present)
