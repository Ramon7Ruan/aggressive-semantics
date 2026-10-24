"""规范化层：把「原始文本」变成「可计算文本」。

这一层只做**确定性的字符串与时间处理**，不引入任何判断性标注。
所有函数都是纯函数，同一输入永远同一输出——这是可复现性的前提。
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import date, datetime, timedelta

from . import config

# ── 清洗 ──────────────────────────────────────────────────────────

_RE_TAG = re.compile(r"<[^>]{1,200}>")
_RE_URL = re.compile(r"https?://\S+|www\.\S+")
_RE_SPACE = re.compile(r"[ \t\u00a0\u3000]+")
_RE_BLANKLINE = re.compile(r"\n{3,}")
# 零宽字符与 BOM：复制粘贴与网页里常见，会污染字符计数
_RE_ZERO = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")


def clean_text(raw: str) -> str:
    """清洗正文。

    做：去 HTML 标签、反转义实体、统一全角空格、压空白、去零宽字符、统一换行。
    不做：去 emoji（emoji 本身是指标，见 metrics.lexical）、去标点（标点是指标）、
          繁简转换（会改变用词，属于研究者判断，不放进通用层）。
    """
    if not raw:
        return ""
    s = str(raw)
    s = unicodedata.normalize("NFC", s)
    s = _RE_ZERO.sub("", s)
    s = _RE_TAG.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _RE_SPACE.sub(" ", s)
    s = _RE_BLANKLINE.sub("\n\n", s)
    # 行首行尾空白去掉，但保留换行结构（段落数是指标）
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def strip_urls(text: str) -> str:
    """去掉 URL 后的文本。算词汇丰富度时必须先去掉，否则链接字符会稀释指标。"""
    return _RE_URL.sub(" ", text)


def anonymize(author: str | None, salt: str | None = None) -> str | None:
    """作者标识哈希。截 10 位十六进制，碰撞率在 ≤10 万人时低于 1e-4。"""
    if author is None or str(author).strip() == "":
        return None
    salt = config.HASH_SALT if salt is None else salt
    h = hashlib.sha1(f"{salt}|{str(author).strip()}".encode("utf-8")).hexdigest()
    return "u_" + h[:10]


# ── 时间 ──────────────────────────────────────────────────────────

_RE_FULL = re.compile(
    r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"
    r"(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)
_RE_MD = re.compile(r"(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?(?:\s*(\d{1,2}):(\d{2}))?")
_RE_REL = re.compile(r"(\d+)\s*(秒|分钟|小时|天)前")


def parse_time(raw: str | None, *, ref_year: int | None = None,
               ref: datetime | None = None) -> datetime | None:
    """解析时间字符串。

    支持：ISO、`2024-05-01 12:30`、`2024年5月1日`、`5月1日`（用 ref_year 补年）、
          `3小时前`（用 ref 反推）。
    **不做**：猜测时区。无时区信息时按 naive 处理，与 ref 保持同一基准。

    返回 None 表示无法解析——调用方必须把它当作「时间缺失」，不能填 0 或今天。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    m = _RE_FULL.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        try:
            return datetime(y, mo, d, hh, mm, ss)
        except ValueError:
            return None

    m = _RE_MD.search(s)
    if m and ref_year is not None:
        mo, d = int(m.group(1)), int(m.group(2))
        hh = int(m.group(3) or 0)
        mm = int(m.group(4) or 0)
        try:
            return datetime(ref_year, mo, d, hh, mm)
        except ValueError:
            return None

    m = _RE_REL.search(s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        base = ref or datetime.now()
        delta = {
            "秒": timedelta(seconds=n),
            "分钟": timedelta(minutes=n),
            "小时": timedelta(hours=n),
            "天": timedelta(days=n),
        }[unit]
        return base - delta

    # 纯数字时间戳（秒或毫秒）
    if s.isdigit() and len(s) >= 10:
        ts = int(s[:10]) if len(s) > 10 else int(s)
        try:
            return datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def year_of(dt: datetime | None) -> int | None:
    return dt.year if dt else None


# ── 计数 ──────────────────────────────────────────────────────────

_RE_NOSPACE = re.compile(r"\s+")
_RE_CJK = re.compile(r"[\u4e00-\u9fff]")
_RE_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def char_count(text: str) -> int:
    """有效字符数：去掉所有空白后的长度。中文一个字算 1，英文一个字母算 1。

    与「字数」的中文习惯一致（Word 的字符数不含空格）。
    """
    return len(_RE_NOSPACE.sub("", text or ""))


def word_count(text: str) -> int:
    """词数：中文按字，英文按词，混排时相加。"""
    t = text or ""
    return len(_RE_CJK.findall(t)) + len(_RE_LATIN_WORD.findall(t))


def para_count(text: str) -> int:
    return len([p for p in (text or "").split("\n") if p.strip()])


# ── 分句 ──────────────────────────────────────────────────────────

_RE_SENT_END = re.compile(r"(?<=[。！？!?；;…])|(?<=\.)\s+")
_RE_SENT_KEEP = re.compile(r"[。！？!?；;…]")


def split_sentences(text: str, *, min_chars: int | None = None) -> list[str]:
    """分句。

    以 。！？!?；;… 与英文句点+空格为界。过短片段并入上一句
    （「对。」这类独立成句会污染平均句长）。
    """
    min_chars = config.MIN_SENT_CHARS if min_chars is None else min_chars
    t = (text or "").strip()
    if not t:
        return []
    parts = [p.strip() for p in _RE_SENT_END.split(t) if p and p.strip()]
    out: list[str] = []
    for p in parts:
        # 用 <= ：像「对。」「嗯。」这种 2 字片段必须并入上一句，
        # 否则平均句长会被系统性拉低。
        if out and char_count(p) <= min_chars:
            out[-1] = out[-1] + p
        else:
            out.append(p)
    return out


def has_sentence_end(text: str) -> bool:
    return bool(_RE_SENT_KEEP.search(text or ""))


# ── 分词（供词汇丰富度使用）──────────────────────────────────────

_jieba = None
_jieba_tried = False


def _get_jieba():
    global _jieba, _jieba_tried
    if not _jieba_tried:
        _jieba_tried = True
        try:  # pragma: no cover - 取决于本机是否装了 jieba
            import jieba  # type: ignore

            jieba.setLogLevel(60)
            _jieba = jieba
        except Exception:
            _jieba = None
    return _jieba


_RE_TOKEN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+|\d+")


def tokenize(text: str) -> list[str]:
    """分词。有 jieba 用 jieba，否则退回「汉字逐字 + 英文整词 + 数字整段」。

    退回方案不是降级妥协：MATTR 衡量的是「滑动窗口内不重复 token 的比例」，
    字级切分同样能刻画用词重复度，只是对多字词不敏感。报告中会注明所用切分器，
    因为**跨切分器的数值不可直接比较**——这是必须写进方法节的限制。
    """
    t = strip_urls(text or "")
    jb = _get_jieba()
    if jb is not None:  # pragma: no cover
        return [w for w in jb.lcut(t) if w.strip() and not re.fullmatch(r"[\W_]+", w)]
    return _RE_TOKEN.findall(t)


def tokenizer_name() -> str:
    return "jieba" if _get_jieba() is not None else "char"


# ── 记录级规范化 ──────────────────────────────────────────────────

def normalize_record(rec, *, ref_year: int | None = None, ref: datetime | None = None):
    """就地规范化一条 Record 并返回它。

    - text 清洗
    - 作者哈希
    - posted_at 解析成 datetime（存到 rec.created_at），原字符串保留
    - 无时间时**不猜测**，留 None
    """
    rec.text = clean_text(rec.text)
    rec.author_id = anonymize(rec.author_id)
    dt = parse_time(rec.posted_at, ref_year=ref_year, ref=ref)
    rec.created_at = dt
    if rec.quote_text:
        rec.quote_text = clean_text(rec.quote_text)
        rec.has_quote = True
    if rec.unit == "opening":
        rec.is_op = True
    return rec


def normalize_all(records, *, ref: datetime | None = None):
    """按 thread 推断年份：先用主楼年份给同帖缺年份的回复补年。"""
    # 第一遍：主楼时间
    thread_year: dict[str, int] = {}
    for r in records:
        if r.unit == "opening":
            dt = parse_time(r.posted_at, ref=ref)
            if dt:
                thread_year[r.thread_id] = dt.year
    for r in records:
        normalize_record(r, ref_year=thread_year.get(r.thread_id), ref=ref)
    return records


def today() -> date:
    return date.today()
