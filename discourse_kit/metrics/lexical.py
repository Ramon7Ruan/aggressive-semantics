"""B 组：话语形态指标。

这一组回答的是「这段话长什么样」，不涉及立场对错。
所有「密度」类指标统一用 **每 100 字符** 为单位，便于跨长度比较。
"""

from __future__ import annotations

import re

from .. import config
from ..normalize import char_count, split_sentences, strip_urls, tokenize
from ..schema import Record
from .lexicon import slang
from .registry import metric

_RE_QUESTION_END = re.compile(r"[？?]\s*$")
_RE_EXCL = re.compile(r"[！!]")
_RE_WH = re.compile(r"为什么|为何|怎么|怎样|如何|是否|什么|难道|多少|哪")
_RE_FIRST = re.compile(r"我们|咱们|咱|我")
_RE_SECOND = re.compile(r"你们|您|你")


def _per100(n: float, text: str) -> float:
    c = char_count(text)
    return n * 100 / c if c else 0.0


def mattr(tokens: list[str], window: int) -> float | None:
    """移动平均类符-形符比。

    为什么必须用 MATTR 而不是 TTR：TTR = 独特词数 / 总词数，会随文本变长
    单调下降。直接比较 200 字回复与 5000 字主楼，必然得出「短的词汇更丰富」。
    MATTR 在固定窗口内计算再平均，长度被消掉了。
    """
    if len(tokens) < window:
        return None
    ttrs = []
    for i in range(len(tokens) - window + 1):
        win = tokens[i:i + window]
        ttrs.append(len(set(win)) / window)
    return sum(ttrs) / len(ttrs)


@metric("B0_len", group="B", name="字符数", min_chars=1,
        note="分析的基准变量。极短文本上的密度类指标天然不稳，"
             "任何组间比较都应同时报告长度，否则差异可能只是长度的差异")
def b0(rec: Record) -> int:
    return char_count(rec.text)


@metric("B1_mattr", group="B", name="词汇丰富度 MATTR",
        min_chars=config.MATTR_MIN_TOKENS,
        note=f"窗口={config.MATTR_WINDOW} token；文本不足 {config.MATTR_MIN_TOKENS} 字不计算")
def b1(rec: Record) -> float | None:
    toks = tokenize(strip_urls(rec.text))
    return mattr(toks, config.MATTR_WINDOW)


@metric("B2_ttr", group="B", name="类符形符比 TTR",
        min_chars=config.MATTR_MIN_TOKENS,
        note="仅作对照；随长度单调下降，禁止跨长度直接比较，结论以 B1 为准")
def b2(rec: Record) -> float | None:
    toks = tokenize(strip_urls(rec.text))
    if not toks:
        return None
    return len(set(toks)) / len(toks)


@metric("B3_sent_len", group="B", name="平均句长（字）", min_chars=8,
        needs=("sentence",),
        note="过短的碎片句会被 normalize.split_sentences 并入上一句；"
             "语料无句末标点时该指标退化为字符数，故声明 needs")
def b3(rec: Record) -> float | None:
    sents = split_sentences(rec.text)
    if not sents:
        return None
    return sum(char_count(s) for s in sents) / len(sents)


@metric("B4_sent_len_cv", group="B", name="句长变异系数", min_chars=30,
        needs=("sentence",),
        note="标准差/均值。需要至少 2 句，否则 None")
def b4(rec: Record) -> float | None:
    sents = split_sentences(rec.text)
    if len(sents) < 2:
        return None
    lens = [char_count(s) for s in sents]
    mean = sum(lens) / len(lens)
    if mean == 0:
        return None
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    return (var ** 0.5) / mean


@metric("B5_question_rate", group="B", name="问句占比", min_chars=8,
        needs=("sentence",), note="以句末问号判定；无标点语料上不可用")
def b5(rec: Record) -> float | None:
    sents = split_sentences(rec.text)
    if not sents:
        return None
    q = sum(1 for s in sents if _RE_QUESTION_END.search(s))
    return q / len(sents)


@metric("B6_excl_density", group="B", name="感叹号密度（每百字）", min_chars=5)
def b6(rec: Record) -> float:
    return _per100(len(_RE_EXCL.findall(rec.text)), rec.text)


@metric("B7_wh_density", group="B", name="疑问词密度（每百字）", min_chars=5,
        note="疑问词密度高但问句占比低，通常意味着反问句——修辞性施压而非真提问")
def b7(rec: Record) -> float:
    return _per100(len(_RE_WH.findall(rec.text)), rec.text)


@metric("B8_first_person", group="B", name="第一人称密度（每百字）", min_chars=5,
        note="自我涉入程度。求助型话题应显著高于解释型")
def b8(rec: Record) -> float:
    return _per100(len(_RE_FIRST.findall(rec.text)), rec.text)


@metric("B9_second_person", group="B", name="第二人称密度（每百字）", min_chars=5,
        note="对话性/对抗性的语言标记；攻击性文本中常显著升高")
def b9(rec: Record) -> float:
    return _per100(len(_RE_SECOND.findall(rec.text)), rec.text)


@metric("B10_slang_density", group="B", name="网络用语密度（每百字）", min_chars=10,
        note="语域口语化程度；也用作跨平台样本清洁度检查")
def b10(rec: Record) -> float | None:
    return slang().density(rec.text, min_chars=10)
