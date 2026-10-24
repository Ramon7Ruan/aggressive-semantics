"""统计工具：效应量优先，p 值其次。

为什么不用 scipy：本项目样本量常在几十到几百之间、分布严重右偏（发言长度、
点赞数都是长尾），t 检验的正态假设站不住。改用**置换检验**，它只要求
「可交换性」，对分布形状无要求，且实现透明、可被审稿人逐行核对。

为什么不报 p 值为主：n 足够大时任何微小差异都显著。所以报告先给效应量，
并且只有 |d| ≥ 0.2 才允许使用「更倾向于」这类比较措辞（config.MIN_EFFECT_FOR_CLAIM）。
"""

from __future__ import annotations

import math
import random
from typing import Sequence

from . import config


def mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def sd(xs: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def cohen_d(a: Sequence[float], b: Sequence[float]) -> float | None:
    """标准化均值差 (mean_a − mean_b) / pooled_sd。"""
    if len(a) < 2 or len(b) < 2:
        return None
    sa, sb = sd(a), sd(b)
    if sa is None or sb is None:
        return None
    na, nb = len(a), len(b)
    pooled = math.sqrt(((na - 1) * sa ** 2 + (nb - 1) * sb ** 2) / (na + nb - 2))
    if pooled == 0:
        return 0.0
    return (sum(a) / na - sum(b) / nb) / pooled


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float | None:
    """非参数效应量：P(a>b) − P(a<b)，范围 [-1,1]。对长尾分布比 d 稳健。"""
    if not a or not b:
        return None
    gt = lt = 0
    for x in a:
        for y in b:
            if x > y:
                gt += 1
            elif x < y:
                lt += 1
    return (gt - lt) / (len(a) * len(b))


def permutation_test(a: Sequence[float], b: Sequence[float], *,
                     n_perm: int | None = None, seed: int = 7) -> float:
    """双侧置换检验，统计量为均值差。返回 p 值。"""
    n_perm = n_perm or config.PERMUTATION_N
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    obs = abs(sum(a) / len(a) - sum(b) / len(b))
    pool = list(a) + list(b)
    na = len(a)
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        diff = abs(sum(pool[:na]) / na - sum(pool[na:]) / (len(pool) - na))
        if diff >= obs:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def bootstrap_ci(xs: Sequence[float], *, n_boot: int | None = None,
                 alpha: float = 0.05, seed: int = 11) -> tuple[float, float] | None:
    """均值的百分位 bootstrap 置信区间。样本 < 5 直接返回 None。"""
    n_boot = n_boot or config.BOOTSTRAP_N
    if len(xs) < 5:
        return None
    rng = random.Random(seed)
    n = len(xs)
    means = []
    for _ in range(n_boot):
        s = sum(xs[rng.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    lo = means[int(n_boot * alpha / 2)]
    hi = means[int(n_boot * (1 - alpha / 2)) - 1]
    return (lo, hi)


def bh_fdr(pvals: Sequence[float], alpha: float | None = None) -> list[bool]:
    """Benjamini–Hochberg 控制 FDR。多假设检验时必须用，否则必然出现假阳性。"""
    alpha = config.ALPHA if alpha is None else alpha
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    keep = [False] * m
    threshold_rank = 0
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= alpha * rank / m:
            threshold_rank = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= threshold_rank:
            keep[idx] = True
    return keep


def effect_magnitude(d: float | None) -> str:
    if d is None:
        return "NA"
    a = abs(d)
    if a < 0.2:
        return "可忽略"
    if a < 0.5:
        return "小"
    if a < 0.8:
        return "中"
    return "大"


def describe(xs: Sequence[float]) -> dict:
    vals = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 4),
        "median": round(median(vals), 4),
        "sd": round(sd(vals), 4) if sd(vals) is not None else None,
        "p25": round(sorted(vals)[len(vals) // 4], 4),
        "p75": round(sorted(vals)[(3 * len(vals)) // 4], 4),
        "max": round(max(vals), 4),
    }


# ── 话题级（聚类）推断 ────────────────────────────────────────────
#
# 为什么必须做这一层：一条微博评论不是一次独立观测。同一话题下的几千条
# 评论共享同一个议题、同一批事件、同一段舆论语境，把它们当成 n=14705 的
# 独立样本，p 值会被压到极小，任何微小差异都「显著」。这在文献里叫
# pseudoreplication，是内容分析最常见的统计错误。
#
# 做法：把**话题**当抽样单位。每个话题先算一个汇总值（中位数），
# 再在话题层做检验。16 个话题里选 6 个的组合数只有 8008，
# 可以**穷举**所有分组，得到精确 p 值，不需要抽样近似。


def ranks(xs: Sequence[float]) -> list[float]:
    """平均秩（并列取均值）。"""
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """秩相关。不用 scipy：只要秩变换后算 Pearson，实现可逐行核对。"""
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def spearman_p(a: Sequence[float], b: Sequence[float], *,
               n_perm: int | None = None, seed: int = 13) -> float:
    """置换检验给出 Spearman 的 p 值（双侧）。"""
    n_perm = n_perm or config.PERMUTATION_N
    obs = spearman(a, b)
    if obs is None:
        return float("nan")
    rng = random.Random(seed)
    bb = list(b)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(bb)
        r = spearman(a, bb)
        if r is not None and abs(r) >= abs(obs) - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def cluster_permutation(values: dict[str, float], groups: dict[str, str],
                        a_codes: Sequence[str], b_codes: Sequence[str],
                        *, max_exact: int = 200_000, n_perm: int | None = None,
                        seed: int = 17) -> dict:
    """话题级精确置换检验。

    统计量 = median(A 组话题值) − median(B 组话题值)。
    每个话题先汇总成一个值（调用方保证），于是 n 就是话题数，
    不会因为评论多而虚增。

    返回 {status, n_a, n_b, stat, p, values_a, values_b, exact}
    """
    a_ids = [k for k, g in groups.items() if g in a_codes and k in values]
    b_ids = [k for k, g in groups.items() if g in b_codes and k in values]
    va = [values[k] for k in a_ids]
    vb = [values[k] for k in b_ids]
    if len(va) < 3 or len(vb) < 3:
        return {"status": "insufficient_topics", "n_a": len(va), "n_b": len(vb),
                "values_a": va, "values_b": vb}

    ma, mb = median(va), median(vb)
    obs = (ma or 0) - (mb or 0)
    pool = va + vb
    n, k = len(pool), len(va)

    from math import comb
    total = comb(n, k)
    exact = total <= max_exact

    def _stat(idx_a: Sequence[int]) -> float:
        take = set(idx_a)
        sa = [pool[i] for i in take]
        sb = [pool[i] for i in range(n) if i not in take]
        return (median(sa) or 0) - (median(sb) or 0)

    tol = 1e-12
    hits = 0
    if exact:
        from itertools import combinations
        for combo in combinations(range(n), k):
            if abs(_stat(combo)) >= abs(obs) - tol:
                hits += 1
        p = hits / total
    else:
        n_perm = n_perm or config.PERMUTATION_N
        rng = random.Random(seed)
        idx = list(range(n))
        for _ in range(n_perm):
            rng.shuffle(idx)
            if abs(_stat(idx[:k])) >= abs(obs) - tol:
                hits += 1
        p = (hits + 1) / (n_perm + 1)

    return {
        "status": "ok", "n_a": len(va), "n_b": len(vb),
        "stat": round(obs, 4), "p": round(p, 4), "exact": exact,
        "values_a": [round(v, 4) for v in va],
        "values_b": [round(v, 4) for v in vb],
    }


def sign_test(diffs: Sequence[float]) -> dict:
    """符号检验：n 个配对差值中正负各多少。用于话题级方向一致性。"""
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if n == 0:
        return {"status": "empty", "pos": 0, "neg": 0}
    # 双侧精确二项
    from math import comb
    k = min(pos, neg)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"status": "ok", "pos": pos, "neg": neg, "p": round(min(1.0, 2 * tail), 4)}


def icc_oneway(groups: dict[str, Sequence[float]]) -> dict | None:
    """单因素随机效应 ICC(1)：组间方差占总方差的比例。

    用途很具体：先回答「话题是否真的解释了发言差异」。
    若 ICC 很低（例如 < 0.05），说明同一话题内部的分歧远大于话题之间，
    那么按话题类型分组做比较本身就没有多大意义——这一步先做，
    可以避免把「组间无差异」误读成「类型不影响话语形态」。

    不平衡设计用 ANOVA 估计量（k 取平均组大小）。
    """
    vals = {k: [v for v in vs if v is not None] for k, vs in groups.items()}
    vals = {k: v for k, v in vals.items() if v}
    if len(vals) < 2:
        return None
    allv = [x for v in vals.values() for x in v]
    N = len(allv)
    k = len(vals)
    if N <= k:
        return None
    grand = sum(allv) / N
    ss_b = sum(len(v) * (sum(v) / len(v) - grand) ** 2 for v in vals.values())
    ss_w = sum(sum((x - sum(v) / len(v)) ** 2 for x in v) for v in vals.values())
    df_b, df_w = k - 1, N - k
    if df_b <= 0 or df_w <= 0:
        return None
    ms_b = ss_b / df_b
    ms_w = ss_w / df_w
    n0 = (N - sum(len(v) ** 2 for v in vals.values()) / N) / (k - 1)
    denom = ms_b + (n0 - 1) * ms_w
    icc = (ms_b - ms_w) / denom if denom > 0 else None
    return {
        "icc": round(max(0.0, min(1.0, icc)), 4) if icc is not None else None,
        "n_groups": k, "n_obs": N,
        "ms_between": round(ms_b, 4), "ms_within": round(ms_w, 4),
    }
