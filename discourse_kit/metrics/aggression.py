"""G 组：攻击性语言指标。

方法上的硬约束：**本组不得只靠词典。**

原因是一个具体的、可验证的问题：中文社区（贴吧/虎扑/微博）的攻击大量以
「褒义词反用」出现——「睿智」「典」「孝」「急了」「含金量」。通用情感词典会把
这类文本判为**正面**，侮辱词表则一个都不命中。于是词典法会系统性地漏掉
最具攻击性的那批文本，而这些漏掉的样本恰恰是研究的核心对象。

因此本组的定位是：**词典给出可复现的下界，LLM/人工标注给出上界**，
两个数都要报告。若两者差距过大（例如 LLM 判为高攻击而词典判 0），
说明反讽漏检严重，必须在方法节披露，而不是取平均掩盖掉。

G1 是复合分（0–5），其余为分量。分量全部保留，便于解释 G1 的变化来源。
"""

from __future__ import annotations

import re

from ..normalize import char_count, split_sentences
from ..schema import Record
from .lexicon import booster, group_label, insult, irony
from .registry import metric

_RE_SECOND = re.compile(r"你们|您|你")
_RE_QUESTION = re.compile(r"[？?]")
_RE_UNIVERSAL = re.compile(r"都|全都|一律|无一|没一个|个个|人人|清一色|这种|这类|这群")
_RE_PERSON = re.compile(r"他|她|这人|这种人|此人|楼主|层主|楼上|楼下|作者")
_RE_OPINION = re.compile(r"观点|说法|逻辑|论点|结论|理由|论证|看法|意见")

# ── 群体名词规则 ──────────────────────────────────────────────────
#
# 为什么要有这条规则：词表里没有「外国人」「文科生」「韩国人」这类
# 由事件临时构成的群体，而微博议题恰恰大量使用它们。只靠词表，
# G5 在语料里只有 0.1% 触发——不是这些文本没有群体标签，是词表够不着。
#
# 规则不是随便加的，它经过金标准检验（COLD test，n=7,680）：
#   | 判定方式                    | 攻击群体 | 安全  | 绝对差 |
#   | 词表 + 全称（原版）          |  10.3%  | 2.9% | 7.4pp |
#   | 词表 + 群体名词 + 邻近全称    |  17.1%  | 7.4% | 9.7pp |
#   | 词表 + 群体名词（去掉全称）   |  77.8%  |51.8% | 26.0pp（但噪声过大）|
# 第三行看似差最大，实则是「凡提群体即命中」，已在 52% 的安全文本上误报，
# 不能用来比较组间差异。故选第二种。
#
# 「邻近」是必须的：「很多人**都**这么说」和「河南人**都**这样」都含全称词，
# 但只有后者是标签化。要求全称词出现在名词后 8 字内，可把前者排除。
_RE_GROUP_NOUN = re.compile(r"[\u4e00-\u9fff]{1,3}(?:人|族|生|员|民)")
_BAD_PREFIX = ("很多", "有些", "有的", "每个", "所有", "一个", "两个", "几个",
               "这个", "那个", "什么", "啥", "多少")
_UNIV_NEAR = re.compile(r"都|全都|一律|无一|没一个|个个|人人|清一色|这种|这类|这群|整个")


def group_nouns(text: str) -> list[str]:
    """抽出「X人/X生/X族」式群体指称，要求其后 8 字内有全称量词。"""
    out: list[str] = []
    if not text:
        return out
    for m in _RE_GROUP_NOUN.finditer(text):
        s = m.group(0)
        if len(s) < 2 or s.startswith(_BAD_PREFIX):
            continue
        if _UNIV_NEAR.search(text[m.end():m.end() + 8]):
            out.append(s)
    return out


def _hits(text: str, pat: re.Pattern) -> int:
    return len(pat.findall(text))


def _irony_parts(text: str) -> tuple[float, float, int]:
    hits = irony().find(text)
    strong = sum(1 for t in hits if t.weight >= 1.0)
    weak = sum(1 for t in hits if t.weight < 1.0)
    return strong * 0.8, weak * 0.3, len(hits)


def group_labeling(text: str) -> int:
    """群体标签化：群体指称 + 全称量词同现才计 1。

    只出现「河南人」不计——那可能只是描述。出现「河南人都……」才计。
    这个区分是 G5 可信度的全部来源。

    群体指称来自两处：词表（小镇做题家、田园女权…）+ 名词规则（X人/X生/X族）。
    规则的作用是把「外国人」「文科生」这类由事件临时构成的群体纳入，
    加它的依据与实测对比写在模块常量旁，不是凭感觉。
    """
    hit = (group_label().count(text) > 0 and
           _RE_UNIVERSAL.search(text) is not None)
    return int(hit or bool(group_nouns(text)))


def target_of(text: str) -> str:
    """攻击对象：none / opinion / person / group。

    判定顺序即优先级：群体 > 个人 > 观点。理由是越靠后的对象越「对事」，
    越靠前的越「对人」——顺序反了会把「群体攻击」误记为「观点分歧」。
    """
    has_insult = insult().count(text) > 0
    has_irony = irony().count(text) > 0
    aggressive = has_insult or has_irony
    if not aggressive:
        return "none"
    if group_labeling(text):
        return "group"
    if has_insult and (_RE_PERSON.search(text) or _RE_SECOND.search(text)):
        return "person"
    if _RE_SECOND.search(text) and has_irony:
        return "person"
    if _RE_OPINION.search(text):
        return "opinion"
    if has_insult:
        return "person"
    return "opinion"


def form_of(text: str) -> str:
    """攻击形式（取最主要的一种）。"""
    if insult().count(text):
        return "insult"
    if group_labeling(text):
        return "labeling"
    strong, weak, n = _irony_parts(text)
    if n:
        return "irony"
    if _RE_QUESTION.search(text) and _RE_SECOND.search(text):
        return "rhetorical"
    if booster().count(text) and _RE_SECOND.search(text):
        return "derogation"
    return "none"


def intensity(text: str) -> float:
    """G1：0（无攻击）或 1–5。

    **必须先有攻击词或反讽信号，才谈强度。**
    否则「我该不该回去？我很纠结」这种普通问句会因为带问号而被判成 1.25 分攻击性——
    这是最容易出现的假阳性，也是这类指标最常被批评的地方。

    分量：侮辱词（上限 2.5）/ 反讽（上限 1.5）/ 施压（第二人称 + 反问，上限 1.0）
          / 绝对化（上限 0.5）。后两者只放大已有信号，不能单独起分。
    """
    insult_w = sum(t.weight for t in insult().find(text))
    s_irony, w_irony, _ = _irony_parts(text)
    if insult_w == 0 and s_irony + w_irony == 0:
        return 0.0

    pressure = min(1.0, 0.25 * (_hits(text, _RE_SECOND) + _hits(text, _RE_QUESTION)))
    boost = min(0.5, 0.10 * booster().count(text))

    raw = min(2.5, 0.9 * insult_w) + min(1.5, s_irony + w_irony) + pressure + boost
    return round(min(5.0, 1.0 + raw), 2)


@metric("G1_intensity", group="G", name="攻击强度 0–5", min_chars=3,
        note="复合分：侮辱词/反讽/施压/绝对化加权。0 = 无攻击信号，1–5 = 有信号且递增")
def g1(rec: Record) -> float:
    return intensity(rec.text)


@metric("G2_target", group="G", name="攻击对象", min_chars=3, kind="categorical",
        note="none/opinion/person/group。优先级：群体>个人>观点")
def g2(rec: Record) -> str:
    return target_of(rec.text)


@metric("G3_form", group="G", name="攻击形式", min_chars=3, kind="categorical",
        note="insult/labeling/irony/rhetorical/derogation/none")
def g3(rec: Record) -> str:
    return form_of(rec.text)


@metric("G4_insult_density", group="G", name="侮辱词密度（每百字）", min_chars=5,
        note="词典法下界。与 LLM 标注对照，差距即反讽漏检量")
def g4(rec: Record) -> float | None:
    return insult().density(rec.text, min_chars=5)


@metric("G5_group_label", group="G", name="群体标签化", min_chars=5,
        kind="categorical",
        note="群体词 + 全称量词同现。二者缺一不计，否则会把中性提及误判为标签化")
def g5(rec: Record) -> int:
    return group_labeling(rec.text)


@metric("G6_pressure", group="G", name="对话施压强度", min_chars=5,
        needs=("sentence",),
        note="(第二人称次数 + 问号数) ÷ 句数。用于区分「自述」与「逼问」；"
             "除数为句数，故依赖分句")
def g6(rec: Record) -> float | None:
    sents = split_sentences(rec.text)
    if not sents:
        return None
    return (_hits(rec.text, _RE_SECOND) + _hits(rec.text, _RE_QUESTION)) / len(sents)


@metric("G7_irony", group="G", name="反讽标记数", min_chars=3,
        note="褒词反用命中的加权计数；单列出来是因为它是词典法的主要漏检来源")
def g7(rec: Record) -> float:
    strong, weak, _ = _irony_parts(rec.text)
    return round(strong + weak, 2)
