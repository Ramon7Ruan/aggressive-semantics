"""帖级（thread-level）特征。

为什么必须单独有一层：**主楼和回复不是同一种文本。**
实测样本里主楼 813 字、回复中位 32 字，二者是不同成本档位的产物。
把它们混在一起算平均句长，得到的是「主楼字数」的代理，不是「用户说话方式」。

所以分析单元分两级：
- 单元级（record）：只用于同类比较（回复 vs 回复、主楼 vs 主楼）
- 帖级（thread）：用来看一个话题内部的演化，如「攻击性是否随楼层上升」
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd

from ..normalize import char_count
from ..schema import UNIT_OPENING, UNIT_REPLY, Record


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """最小二乘斜率。样本少于 3 或 x 无变异时返回 None。"""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / var


def thread_table(records: Iterable[Record],
                 metrics_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """按 thread_id 汇总。若给出 metrics_df，额外计算帖内指标斜率。"""
    buckets: dict[str, list[Record]] = {}
    for r in records:
        buckets.setdefault(r.thread_id, []).append(r)

    rows: list[dict] = []
    for tid, rs in buckets.items():
        openings = [r for r in rs if r.unit == UNIT_OPENING]
        replies = [r for r in rs if r.unit == UNIT_REPLY]
        op = openings[0] if openings else None
        rep_lens = [char_count(r.text) for r in replies]

        # 时间
        times = sorted(r.created_at for r in rs if r.created_at is not None)
        span_h = ((times[-1] - times[0]).total_seconds() / 3600) if len(times) >= 2 else None
        latency = None
        if op is not None and op.created_at is not None:
            later = [r.created_at for r in replies
                     if r.created_at is not None and r.created_at >= op.created_at]
            if later:
                latency = (min(later) - op.created_at).total_seconds() / 60

        # 参与集中度
        authors = [r.author_id for r in replies if r.author_id]
        uniq = len(set(authors))
        conc = None
        if authors:
            from collections import Counter

            c = Counter(authors)
            shares = [v / len(authors) for v in c.values()]
            conc = sum(s * s for s in shares)  # Herfindahl

        row = {
            "thread_id": tid,
            "topic_id": next((r.topic_id for r in rs if r.topic_id), None),
            "topic_title": op.topic_title if op and op.topic_title else
                           next((r.topic_title for r in rs if r.topic_title), None),
            "platform": next((r.platform for r in rs if r.platform), None),
            "board": next((r.board for r in rs if r.board), None),
            "n_units": len(rs),
            "n_replies": len(replies),
            "opening_chars": char_count(op.text) if op else None,
            "reply_len_median": _median(rep_lens),
            "reply_len_mean": (sum(rep_lens) / len(rep_lens)) if rep_lens else None,
            "reply_len_max": max(rep_lens) if rep_lens else None,
            "span_hours": span_h,
            "first_reply_latency_min": latency,
            "n_authors": uniq,
            "author_hhi": conc,
            "has_stance": next((r.stance for r in rs if r.stance), None),
        }

        # 帖内演化斜率（需要 floor_no + 指标值）
        if metrics_df is not None and not metrics_df.empty:
            sub = metrics_df.reindex([r.doc_id for r in replies])
            floors = [r.floor_no for r in replies]
            if all(f is not None for f in floors) and len(replies) >= 3:
                xs = [float(f) for f in floors]
                for col in ("G1_intensity", "B3_sent_len", "C6_arg_density"):
                    if col in sub.columns:
                        ys = pd.to_numeric(sub[col], errors="coerce")
                        pairs = [(x, y) for x, y in zip(xs, ys) if pd.notna(y)]
                        row[f"slope_{col}"] = (
                            _slope([p[0] for p in pairs], [p[1] for p in pairs])
                            if len(pairs) >= 3 else None)
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["n_units"], ascending=False).reset_index(drop=True)
    return df


def _median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
