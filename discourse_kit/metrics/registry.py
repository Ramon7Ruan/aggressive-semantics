"""指标注册表。

两条硬规则（这是整套指标能被称为「可比较」的前提）：

1. **每个指标必须声明 `requires`。** 缺字段就跳过并记账，不许返回 0。
   0 和「算不出来」在统计上是两件完全不同的事——前者会把均值拉低，
   后者只会减少样本量。混同会直接污染结论。
2. **每个指标必须声明 `min_chars`。** 在 30 字回复上算 MATTR 没有意义，
   它测的是噪声。这不是保守，是有效性边界。
3. **依赖语料形态的指标必须声明 `needs`。** 句长、论证密度这类指标默认
   文本里有句子；若整份语料被去过标点，`split_sentences` 对每条都返回 1 句，
   指标会「正常」返回一个数，而它其实只是字符数。`needs` 用来把这种
   「有值但无意义」标成 `invalid_for_corpus`，不让它混进均值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..schema import UNIT_ARGUMENT, UNIT_OPENING, UNIT_REPLY

ALL_UNITS = (UNIT_OPENING, UNIT_REPLY, UNIT_ARGUMENT)


@dataclass(frozen=True)
class MetricSpec:
    key: str
    group: str            # A 参与 / B 话语形态 / C 论证 / D 立场情绪 / E 社群 / G 攻击
    name: str
    fn: Callable[..., object]
    requires: tuple[str, ...] = ()
    min_chars: int = 0
    units: tuple[str, ...] = ALL_UNITS
    kind: str = "numeric"        # numeric | categorical
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    needs: tuple[str, ...] = ()  # 语料级前提：sentence / paragraph / punctuation


REGISTRY: dict[str, MetricSpec] = {}


def metric(key: str, *, group: str, name: str, requires: tuple[str, ...] = (),
           min_chars: int = 0, units: tuple[str, ...] = ALL_UNITS,
           kind: str = "numeric", note: str = "", tags: tuple[str, ...] = (),
           needs: tuple[str, ...] = ()):
    """注册一个指标。"""

    def deco(fn):
        if key in REGISTRY:
            raise ValueError(f"指标 key 重复：{key}")
        REGISTRY[key] = MetricSpec(
            key=key, group=group, name=name, fn=fn, requires=requires,
            min_chars=min_chars, units=units, kind=kind, note=note, tags=tags,
            needs=needs)
        return fn

    return deco


def get(key: str) -> MetricSpec:
    return REGISTRY[key]


def by_group(group: str) -> list[MetricSpec]:
    return [m for m in REGISTRY.values() if m.group == group]


def all_specs() -> list[MetricSpec]:
    return list(REGISTRY.values())


def codebook() -> list[dict]:
    """导出指标词典（给报告和开源仓库用）。"""
    return [
        {
            "key": s.key,
            "group": s.group,
            "name": s.name,
            "requires": list(s.requires),
            "min_chars": s.min_chars,
            "units": list(s.units),
            "kind": s.kind,
            "note": s.note,
            "tags": list(s.tags),
            "needs": list(s.needs),
        }
        for s in REGISTRY.values()
    ]
