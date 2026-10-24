"""语料级前提校验：防止「指标有值，但值无意义」。

为什么需要这一层：单元级的 `min_chars` 只能挡住「文本太短」，
挡不住「语料整体被改写过」。典型例子是分词后的微博语料——
标点被全部删掉，于是 `split_sentences` 对每条都返回 1 句，
B3 平均句长就退化成「字符数」，C5/C6 的「证据句/论断句」比例
退化成「整段算一句」。这些指标会**正常返回值**，台账上也记 `ok`，
但它们测的不是句法，是长度。审稿人无法从数字上看出来。

所以：指标除了声明 `requires`（字段）与 `min_chars`（单元长度），
还可以声明 `needs`（语料属性）。语料不满足时，该指标整体标记为
`invalid_for_corpus`，并在报告里列出理由——而不是算一个看似正常的数。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from .normalize import char_count, has_sentence_end

# 句末标点。注意：不包含逗号——逗号只能断小句，不足以支撑「句子」这一单位。
_RE_SENT_END = re.compile(r"[。！？!?；;…]")
# 任何标点。用于判断「语料是否被去标点」。
_RE_PUNCT = re.compile(r"[，。！？；：、,.!?;:…（）()《》“”\"'‘’]")


@dataclass
class CorpusProfile:
    """一份语料的形态画像。所有字段都是语料级统计，不是单元级。"""

    n: int = 0
    n_topics: int = 0
    median_chars: float = 0.0
    p90_chars: float = 0.0
    mean_chars: float = 0.0
    short_share_10: float = 0.0      # ≤10 字的占比
    punctuation_rate: float = 0.0    # 含任意标点的单元占比
    sentence_end_rate: float = 0.0   # 含句末标点的单元占比
    paragraph_rate: float = 0.0      # 含换行的单元占比
    tokenized_like: bool = False     # 疑似被空格分词（词间空格但无标点）

    def as_dict(self) -> dict:
        return asdict(self)

    # ── 语料属性判定 ────────────────────────────────────────────
    def check(self, need: str) -> tuple[bool, str]:
        """返回 (是否满足, 不满足时的理由)。未知 need 视为满足（不阻断）。"""
        if need == "sentence":
            ok = self.sentence_end_rate >= 0.50
            return ok, ("" if ok else
                        f"仅 {self.sentence_end_rate:.1%} 的单元含句末标点，"
                        "分句退化为整段，句法类指标不成立")
        if need == "paragraph":
            ok = self.paragraph_rate >= 0.10
            return ok, ("" if ok else
                        f"仅 {self.paragraph_rate:.1%} 的单元含换行，段落类指标不成立")
        if need == "punctuation":
            ok = self.punctuation_rate >= 0.50
            return ok, ("" if ok else
                        f"仅 {self.punctuation_rate:.1%} 的单元含标点，"
                        "标点类指标不成立")
        return True, ""


def profile(records: Iterable, *, topics: Sequence[str] | None = None) -> CorpusProfile:
    """计算语料画像。空语料返回全零（调用方据此跳过全部指标）。"""
    recs = list(records)
    n = len(recs)
    if not n:
        return CorpusProfile()

    texts = [r.text or "" for r in recs]
    chars = sorted(char_count(t) for t in texts)
    n_punct = sum(1 for t in texts if _RE_PUNCT.search(t))
    n_sent = sum(1 for t in texts if has_sentence_end(t))
    n_para = sum(1 for t in texts if "\n" in t.strip())
    # 「疑似分词」：有大量单字之间的空格，却几乎没有标点。
    # 这不是判断真伪，只是提示「这份语料可能不是原始文本」。
    n_spaced = sum(1 for t in texts
                   if t.count(" ") >= max(3, len(t) // 8) and not _RE_PUNCT.search(t))

    topic_ids = set(topics) if topics else {r.thread_id for r in recs}
    return CorpusProfile(
        n=n,
        n_topics=len(topic_ids),
        median_chars=float(chars[n // 2]),
        p90_chars=float(chars[int(n * 0.9)]),
        mean_chars=round(sum(chars) / n, 1),
        short_share_10=round(sum(1 for c in chars if c <= 10) / n, 3),
        punctuation_rate=round(n_punct / n, 3),
        sentence_end_rate=round(n_sent / n, 3),
        paragraph_rate=round(n_para / n, 3),
        tokenized_like=bool(n_spaced / n >= 0.30),
    )


def warnings(p: CorpusProfile) -> list[str]:
    """生成面向研究者的提示语。这些句子会直接进报告，所以要写成人话。"""
    out: list[str] = []
    if p.punctuation_rate < 0.10:
        out.append(
            f"**语料几乎无标点**（含标点 {p.punctuation_rate:.1%}）。"
            "这份语料很可能经过分词或去标点处理，句法、论证结构类指标不可用。"
            "不是「样本少」，而是「测量对象不存在」。")
    if p.median_chars < 20:
        out.append(
            f"**单元极短**（中位 {p.median_chars:.0f} 字，"
            f"{p.short_share_10:.0%} 不超过 10 字）。"
            "词表类指标仍可算，但任何涉及句子组织、修辞结构的结论都不成立。")
    if p.tokenized_like:
        out.append(
            "**疑似分词文本**（大量词间空格且无标点）。"
            "若原平台并非如此书写，则本语料只保留了词汇层信息。")
    return out
