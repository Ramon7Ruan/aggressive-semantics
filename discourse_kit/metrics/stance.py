"""D 组：立场与情绪指标。

原则：**极性、确定性、反讽三者分开算，不合并成一个「情绪分」。**
合并之后无法判断一个高分是因为「强烈反对」还是因为「大量反讽」，
而这两者在研究上指向完全不同的机制。
"""

from __future__ import annotations

import re

from ..normalize import char_count
from ..schema import Record
from . import aggression as _agg
from .lexicon import booster, hedge, sentiment_neg, sentiment_pos
from .registry import metric


def polarity_parts(text: str) -> tuple[float, float]:
    return sentiment_pos().score(text), sentiment_neg().score(text)


@metric("D1_polarity", group="D", name="情绪极性 −1…1", min_chars=5,
        note="(正−负)/(正+负)。无情感词时返回 None——「中性」和「判断不出」不能混同")
def d1(rec: Record) -> float | None:
    p, n = polarity_parts(rec.text)
    if p + n == 0:
        return None
    return (p - n) / (p + n)


@metric("D2_absolutism", group="D", name="绝对化密度（每百字）", min_chars=5,
        note="立场固化的语言标记；争议话题中的核心变量")
def d2(rec: Record) -> float | None:
    return booster().density(rec.text, min_chars=5)


@metric("D3_certainty", group="D", name="确定性 −1…1", min_chars=10,
        note="(绝对化−模糊限制)/(两者之和+1)。正=断言式，负=留有余地")
def d3(rec: Record) -> float | None:
    b = booster().count(rec.text)
    h = hedge().count(rec.text)
    if b + h == 0:
        return None
    return (b - h) / (b + h + 1)


@metric("D4_sentiment_density", group="D", name="情感词密度（每百字）", min_chars=5,
        note="极性值的可靠度前提：密度过低时 D1 不稳定，应视为缺失")
def d4(rec: Record) -> float:
    p, n = polarity_parts(rec.text)
    c = char_count(rec.text)
    return (p + n) * 100 / c if c else 0.0


@metric("D5_polarity_adj", group="D", name="反讽修正后极性 −1…1", min_chars=8,
        note="命中强反讽标记时把极性翻负。这是启发式，必须与 D1 并列报告，不得单独使用")
def d5(rec: Record) -> float | None:
    p, n = polarity_parts(rec.text)
    if p + n == 0:
        return None
    raw = (p - n) / (p + n)
    strong = sum(1 for t in _agg.irony().find(rec.text) if t.weight >= 1.0)
    if strong and raw > 0:
        return -abs(raw)
    return raw


@metric("D6_stance", group="D", name="数据集立场标签", requires=("stance",),
        kind="categorical",
        note="仅在数据集自带立场字段时可用（如辩论语料 pro/con/neutral）")
def d6(rec: Record) -> str | None:
    return rec.stance


@metric("D7_assertive", group="D", name="断言句占比", min_chars=20,
        needs=("sentence",),
        note="以「是/就是/必须/应该」等判断谓词收尾的句子占比，衡量宣示性")
def d7(rec: Record) -> float | None:
    from ..normalize import split_sentences

    sents = split_sentences(rec.text)
    if not sents:
        return None
    pat = re.compile(r"(?:就是|才是|必须是|应该是|一定是|才是真|不接受|不容)")
    return sum(1 for s in sents if pat.search(s)) / len(sents)
