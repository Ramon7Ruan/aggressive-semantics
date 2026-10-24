"""C 组：论证结构指标。

这组是「深度」的操作化。关键是把「长」和「有依据」分开：
字数多可能只是啰嗦，论证密度才反映是否在给理由。

判定口径写死在这里，写论文时必须原样引用，不允许事后调整：
- **证据句**：含数字 / 百分比 / 引号引用 / URL / 年份 / 证据标记词中的任一项
- **论断句**：不含上述任何证据信号，但含评价性或规范性谓词
  （是、应该、必须、不能、说明、意味着、本质、问题、显然……）
- **论证密度 = 证据句数 ÷ 论断句数**。大于 1 表示「举证多于断言」。
"""

from __future__ import annotations

import re

from ..normalize import char_count, split_sentences
from ..schema import Record
from .lexicon import evidence, hedge
from .registry import metric

_RE_LIST = re.compile(
    r"(?m)^\s*(?:\d{1,2}[.、)]|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]、)")
_RE_LIST_WORD = re.compile(r"首先|其次|再次|然后|最后|第一|第二|第三|一方面|另一方面|总之|综上")
_RE_CONCESS = re.compile(r"虽然|尽管|固然|诚然|即使|就算|退一步|不可否认")
_RE_CONTRAST = re.compile(r"但是|但|可是|然而|不过|却|反而")
_RE_NUM = re.compile(r"\d+(?:\.\d+)?%?")
_RE_QUOTE = re.compile(r"[“\"「『][^”\"」』]{2,}[”\"」』]|>")
_RE_URL = re.compile(r"https?://\S+")
_RE_YEAR = re.compile(r"(?:19|20)\d{2}\s*年?")
_RE_CLAIM = re.compile(
    r"应该|必须|不能|不该|说明|表明|意味着|本质|显然是|就是|只能|根源|"
    r"问题在于|原因在于|归根结底|说白了|可见|足以|证明了")


def is_evidence_sentence(sent: str) -> bool:
    """证据句判定。任一形式特征或标记词命中即可。"""
    if _RE_NUM.search(sent) or _RE_QUOTE.search(sent) or _RE_URL.search(sent):
        return True
    if _RE_YEAR.search(sent):
        return True
    return evidence().count(sent) > 0


def is_claim_sentence(sent: str) -> bool:
    """论断句：无证据信号，但有评价/规范谓词。"""
    if is_evidence_sentence(sent):
        return False
    return _RE_CLAIM.search(sent) is not None


def evidence_counts(text: str) -> tuple[int, int, int]:
    """返回 (证据句数, 论断句数, 总句数)。"""
    sents = split_sentences(text)
    ev = sum(1 for s in sents if is_evidence_sentence(s))
    cl = sum(1 for s in sents if is_claim_sentence(s))
    return ev, cl, len(sents)


@metric("C1_para", group="C", name="段落数", min_chars=5,
        needs=("paragraph",),
        note="换行分段。单段长文与多段的论证组织方式不同；单行语料上恒为 1，无信息")
def c1(rec: Record) -> int:
    return len([p for p in rec.text.split("\n") if p.strip()])


@metric("C2_quote", group="C", name="是否引用他人", requires=("has_quote",),
        kind="categorical", note="引用 = 把对方的话拉到自己的论述里，是对话性的强标记")
def c2(rec: Record) -> int:
    return int(bool(rec.has_quote))


@metric("C3_list_rate", group="C", name="列举标记（每百字）", min_chars=20,
        note="编号/序号/首先其次，反映论述是被组织过的")
def c3(rec: Record) -> float:
    n = len(_RE_LIST.findall(rec.text)) + len(_RE_LIST_WORD.findall(rec.text))
    c = char_count(rec.text)
    return n * 100 / c if c else 0.0


@metric("C4_concession", group="C", name="让步结构", min_chars=40,
        kind="categorical",
        note="同时出现「虽然/尽管/固然」与「但是/然而/不过」记为 1。让步是承认对方部分成立")
def c4(rec: Record) -> int:
    return int(bool(_RE_CONCESS.search(rec.text)) and bool(_RE_CONTRAST.search(rec.text)))


@metric("C5_evidence_rate", group="C", name="证据句率", min_chars=20,
        needs=("sentence",),
        note="证据句 ÷ 总句数。与 C6 配合：C5 高而 C6 低说明整段都在举证、缺少主张")
def c5(rec: Record) -> float | None:
    ev, _cl, total = evidence_counts(rec.text)
    return ev / total if total else None


@metric("C6_arg_density", group="C", name="论证密度", min_chars=80,
        needs=("sentence",),
        note="证据句 ÷ 论断句。>1 表示举证多于断言；无论断句时返回 None 而非无穷大")
def c6(rec: Record) -> float | None:
    ev, cl, _t = evidence_counts(rec.text)
    if cl == 0:
        return None
    return ev / cl


@metric("C7_number_density", group="C", name="数字密度（每百字）", min_chars=10,
        note="最廉价也最硬的可核验性指标")
def c7(rec: Record) -> float:
    c = char_count(rec.text)
    return len(_RE_NUM.findall(rec.text)) * 100 / c if c else 0.0


@metric("C8_hedge_density", group="C", name="模糊限制语密度（每百字）", min_chars=10,
        note="「可能/据说/个人认为」。与 C9 一起刻画确定性")
def c8(rec: Record) -> float | None:
    return hedge().density(rec.text, min_chars=10)


@metric("C9_floor", group="C", name="楼层位置", requires=("floor_no",),
        note="用于检验「讨论劣化」：攻击性是否随楼层上升。缺 floor_no 的平台算不出")
def c9(rec: Record) -> int | None:
    return rec.floor_no
