"""词典加载与匹配。

三个刻意的设计：

1. **词典是数据，不是代码。** 扩充词表不需要改 Python，改动可 diff、可审查。
2. **长词优先 + 占位掩码。** 「急了急了」命中后，「急了」不再重复计数。
   否则单个词写两遍就能把密度翻倍，指标可被文本长度无关地操纵。
3. **词表分后缀加权**（@强/@弱/@轻/@中/@重），把「同一个词在不同语境下的
   强度差异」显式化，而不是假装所有命中等价。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from .. import config

_WEIGHT = {"强": 1.0, "弱": 0.4, "重": 3.0, "中": 2.0, "轻": 1.0}


def _lex_path(name: str) -> Path:
    return Path(config.LEXICON_DIR) / f"{name}.txt"


@dataclass(frozen=True)
class Term:
    text: str
    weight: float


class Lexicon:
    """一组带权重的词条。"""

    __slots__ = ("name", "terms", "_by_len")

    def __init__(self, name: str, terms: list[Term]):
        self.name = name
        # 长词优先，保证「典中典」先于「典」被匹配
        self.terms = sorted(terms, key=lambda t: -len(t.text))
        self._by_len = {t.text: t.weight for t in self.terms}

    def __len__(self) -> int:
        return len(self.terms)

    def __contains__(self, s: str) -> bool:
        return s in self._by_len

    def weight_of(self, s: str) -> float:
        return self._by_len.get(s, 0.0)

    def find(self, text: str) -> list[Term]:
        """返回命中的词条（每个位置只算一次，长词优先）。"""
        if not text:
            return []
        buf = list(text)
        hits: list[Term] = []
        for t in self.terms:
            start = 0
            while True:
                i = text.find(t.text, start)
                if i < 0:
                    break
                span = range(i, i + len(t.text))
                if all(buf[j] != "\x00" for j in span):
                    hits.append(t)
                    for j in span:
                        buf[j] = "\x00"
                start = i + len(t.text)
        return hits

    def count(self, text: str) -> int:
        return len(self.find(text))

    def score(self, text: str) -> float:
        return sum(t.weight for t in self.find(text))

    def density(self, text: str, *, per: int = 100, min_chars: int = 1) -> float | None:
        """每 per 个字符的加权命中数。文本过短返回 None（不是 0）。"""
        from ..normalize import char_count

        n = char_count(text)
        if n < min_chars:
            return None
        return self.score(text) * per / n


def parse_lexicon_file(path: Path, default_weight: float = 1.0) -> list[Term]:
    terms: list[Term] = []
    if not path.exists():
        return terms
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        w = default_weight
        if "@" in line:
            head, _, tag = line.rpartition("@")
            if tag in _WEIGHT:
                line, w = head.strip(), _WEIGHT[tag]
        terms.append(Term(line, w))
    return terms


@functools.lru_cache(maxsize=32)
def load(name: str) -> Lexicon:
    return Lexicon(name, parse_lexicon_file(_lex_path(name)))


def irony() -> Lexicon:
    return load("irony")


def insult() -> Lexicon:
    return load("insult")


def booster() -> Lexicon:
    return load("booster")


def hedge() -> Lexicon:
    return load("hedge")


def evidence() -> Lexicon:
    return load("evidence")


def group_label() -> Lexicon:
    return load("group_label")


def slang() -> Lexicon:
    return load("slang")


def sentiment_pos() -> Lexicon:
    return load("sentiment_pos")


def sentiment_neg() -> Lexicon:
    return load("sentiment_neg")


def all_lexicons() -> dict[str, int]:
    """供健康检查：每本词典各有多少条。词典为空是静默失败的常见来源。"""
    names = ("irony", "insult", "booster", "hedge", "evidence",
             "group_label", "slang", "sentiment_pos", "sentiment_neg")
    return {n: len(load(n)) for n in names}
