"""标注一致性度量。

存在理由很简单：**没有一致性系数的分类体系不是测量工具，是个人印象。**

本项目的门槛（写在 config 里）：
- Cohen's κ ≥ 0.70 才允许把该维度用于假设检验
- LLM 辅助标注与人工金标准的 F1 ≥ 0.80 才允许规模化使用

达不到就停下来改编码手册，而不是「差不多能用」。
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """两名编码者的名义一致性。返回 [-1, 1]，1 为完全一致。

    注意：κ 会被「某一类占绝对多数」抬高（kappa paradox）。
    因此必须同时报告每类的 F1 与混淆矩阵，不能只看一个数。
    """
    if len(a) != len(b):
        raise ValueError(f"长度不一致：{len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def krippendorff_alpha_nominal(coders: Sequence[Sequence[str]]) -> float:
    """名义型 Krippendorff α（支持 >2 名编码者、允许缺失 None）。"""
    units = len(coders[0]) if coders else 0
    if units == 0:
        return 0.0
    # 观测到的重合对
    do_num = 0.0
    do_den = 0
    counts: Counter = Counter()
    for u in range(units):
        vals = [c[u] for c in coders if c[u] is not None]
        m = len(vals)
        if m < 2:
            continue
        for i in range(m):
            counts[vals[i]] += 1
            for j in range(m):
                if i != j:
                    do_den += 1
                    do_num += 0 if vals[i] == vals[j] else 1
    if do_den == 0:
        return 0.0
    do = do_num / do_den

    n_total = sum(counts.values())
    if n_total < 2:
        return 0.0
    de = 1 - sum((v / n_total) ** 2 for v in counts.values())
    if de == 0:
        return 1.0
    return 1 - do / de


def precision_recall_f1(y_true: Sequence[str], y_pred: Sequence[str],
                        label: str) -> tuple[float, float, float]:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str],
             labels: Sequence[str]) -> float:
    scores = [precision_recall_f1(y_true, y_pred, l)[2] for l in labels]
    return sum(scores) / len(scores) if scores else 0.0


def confusion(y_true: Sequence[str], y_pred: Sequence[str],
              labels: Sequence[str]) -> dict[tuple[str, str], int]:
    out: Counter = Counter()
    for t, p in zip(y_true, y_pred):
        out[(t, p)] += 1
    return {k: v for k, v in sorted(out.items())}


def report(y_true: Sequence[str], y_pred: Sequence[str],
           labels: Sequence[str]) -> dict:
    """一次性给出 κ / 各类 F1 / macro-F1 / 混淆矩阵。"""
    return {
        "n": len(y_true),
        "kappa": round(cohen_kappa(y_true, y_pred), 3),
        "macro_f1": round(macro_f1(y_true, y_pred, labels), 3),
        "per_label": {
            l: dict(zip(("precision", "recall", "f1"),
                        (round(x, 3) for x in precision_recall_f1(y_true, y_pred, l))))
            for l in labels
        },
        "confusion": {f"{t}->{p}": v for (t, p), v in confusion(y_true, y_pred, labels).items()},
    }
