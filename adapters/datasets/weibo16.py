"""weibo16：16 个社会议题的微博评论 + 立场标签。

来源：Hugging Face `kuroneko5943/weibo16`（Apache-2.0）。
结构：`{train,dev,test}/{话题}.txt`，每行
    id \t 话题 \t 立场(POS/NEG) \t 强度(1/5) \t 空格分词的文本

这个数据集的价值不在规模（1.47 万条），而在于：
1. 16 个话题**全是真实社会议题**（代孕、孙小果、日本核污水、马云谈996、瑞丽疫情…），
   数量刚好少到可以**人工全部编码** T1–T5，而不必抽样；
2. 带立场标签，可以直接验证我们的 D 组（极性/立场）算得对不对。

已知缺陷（必须写进方法局限，不能装作没有）：
- **文本已被分词**，标点与句读全部丢失 → 句长类指标（B3）与依赖句读的
  论证结构指标（C 组）在这份数据上不可用；
- 无楼层、无时间、无作者 → H8「讨论劣化」与任何时间演化分析都做不了。
"""

from __future__ import annotations

import re
from pathlib import Path

PLATFORM = "weibo"
RE_SPLIT = re.compile(r"\t")
RE_CJK = re.compile(r"[\u4e00-\u9fff]|^[，。！？；：、（）《》""''…—]+$")


def _join_tokens(tokens: list[str]) -> str:
    """把分词结果拼回连贯通顺的中文。

    不能简单用空格 join：那样「美国 没 资格」会变成带空格的文本，
    字符级分词器会把空格也当成内容，字数与词汇量都虚高。
    也不能简单用空串 join：那样英文词会粘成一个词。
    规则：含汉字的词直接接上，纯拉丁词前后补空格。
    """
    out = ""
    prev_latin = False
    for tok in tokens:
        if not tok:
            continue
        is_latin = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9'._%-]*", tok))
        if is_latin and out and not prev_latin:
            out += " "
        out += tok
        if is_latin:
            out += " "
        prev_latin = is_latin
    return out.strip()


def to_units(path: str | Path) -> list[dict]:
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(root)

    rows: list[dict] = []
    seen: set[str] = set()
    for split in ("train", "dev", "test"):
        d = root / split
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.txt")):
            topic = f.stem
            text_raw = f.read_text(encoding="utf-8-sig", errors="ignore")
            for ln in text_raw.splitlines():
                if not ln.strip():
                    continue
                parts = RE_SPLIT.split(ln)
                if len(parts) < 5:
                    continue
                # 每个字段都要 strip：原数据集里「韩国吃播道歉」整个话题的
                # id 字段尾部带空格（1089 行）。不 strip 会造出
                # `weibo16-韩国吃播道歉-2078 ` 这种 doc_id，
                # 而 normalize 之后 doc_id 被 strip——同一单元在库里变成两条，
                # 且任何「按 doc_id 回查原文」的下游都会静默失配。
                sid, _topic, stance, _intensity = (p.strip() for p in parts[:4])
                topic = topic.strip()
                body = _join_tokens(" ".join(parts[4:]).split())
                if not body or not sid:
                    continue
                doc_id = f"weibo16-{topic}-{sid}"
                if doc_id in seen:      # 同一 id 跨 split 重复时保留首次出现
                    continue
                seen.add(doc_id)
                rows.append({
                    "doc_id": doc_id,
                    "thread_id": f"weibo16-{topic}",
                    "unit": "reply",
                    "text": body,
                    "platform": PLATFORM,
                    "topic_id": topic,
                    "topic_title": topic,
                    "stance": {"POS": "pro", "NEG": "con"}.get(stance, "neutral"),
                    "source": "weibo16",
                })
    return rows
