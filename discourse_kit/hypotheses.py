"""假设检验：T3（制度指向）vs T5（行动者指向）。

研究收窄后的假设集。原来的 H1–H8 是针对五类框架写的，其中 H1/H2/H3/H7
依赖 T1/T2/T4，而那些类型在微博热搜里根本不存在（详见 v0.4 §5），
所以整体重写，不是删几条了事。

两条方法论约束，贯穿全文件：

1. **推断单位是话题，不是评论。** 16 个话题、6 vs 10 的分组，
   所有 p 值都来自话题级的精确置换检验（穷举 8008 种分组）。
   单元级的 p 值也报，但只作为参考——把 14705 条评论当独立样本，
   会把任何微小差异都推成「显著」。

2. **算不出来就说算不出来。** 缺依赖时状态记 `unavailable`，
   绝不退化成「不显著」。这两件事在论文里的含义完全不同。
"""

from __future__ import annotations

import pandas as pd

from . import config, stats

# 收窄后的两组
T3 = ("T3",)   # 制度指向
T5 = ("T5",)   # 行动者指向


def _need(df: pd.DataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c not in df.columns or df[c].notna().sum() == 0]


def _topic_stat(df: pd.DataFrame, metric: str, how: str = "mean") -> dict[str, float]:
    """每个话题一个汇总值。

    默认用**均值**，不用中位数。理由很具体：G1、G5、G7 这类指标在语料里
    只有 6% 的单元非零，中位数恒为 0——16 个话题全是 0 时，任何话题级检验
    都退化成「0 与 0 无差异」，p 恒等于 1。这不是数据没信号，是统计量选错了。

    均值在话题层面就是「该话题的发言有多大比例/多高的平均水平」，
    正是要比较的量；每个话题有几百到上千条，均值的抽样误差也足够小。
    """
    s = pd.to_numeric(df[metric], errors="coerce")
    tmp = pd.DataFrame({"topic": df["thread_id"], "v": s}).dropna()
    if tmp.empty:
        return {}
    g = tmp.groupby("topic")["v"]
    vals = g.mean() if how == "mean" else g.median()
    return {str(k): float(v) for k, v in vals.items()}


def _binary(series: pd.Series) -> pd.Series:
    return (pd.to_numeric(series, errors="coerce") > 0).astype(float)


def _cmp(df: pd.DataFrame, metric: str, *, higher_in: str,
         binary: bool = False, conditional: bool = False) -> dict:
    """单元级 + 话题级双轨比较。

    `binary=True` 时比较的是「发生率」（该指标 > 0 的单元占比），
    `conditional=True` 时只看已发生的单元（强度，即粗放边际 vs 集约边际）。
    零膨胀指标必须拆成这两个边际来看，否则「有没有说」和「说得有多狠」
    会混成一个数，而这两件事的机制完全不同。
    """
    d0 = df.copy()
    if conditional:
        d0 = d0[pd.to_numeric(d0[metric], errors="coerce") > 0]
    if binary:
        d0["_v"] = _binary(d0[metric])
    else:
        d0["_v"] = pd.to_numeric(d0[metric], errors="coerce")

    a = d0.loc[d0["topic_type"].isin(T3), "_v"].dropna().tolist()
    b = d0.loc[d0["topic_type"].isin(T5), "_v"].dropna().tolist()
    if len(a) < 20 or len(b) < 20:
        return {"status": "insufficient_n", "n_a": len(a), "n_b": len(b)}

    topic_values = _topic_stat(d0, "_v")
    topic_group = (d0.drop_duplicates("thread_id")[["thread_id", "topic_type"]]
                   .set_index("thread_id")["topic_type"].to_dict())
    cluster = stats.cluster_permutation(topic_values, topic_group, T3, T5)

    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    d = stats.cohen_d(a, b)          # = (T3 − T5) / pooled_sd
    delta = stats.cliffs_delta(a[:: max(1, len(a) // 2000)],
                               b[:: max(1, len(b) // 2000)])
    r = {
        "status": "ok",
        "n_a": len(a), "n_b": len(b),
        "mean_a": round(mean_a, 4),
        "mean_b": round(mean_b, 4),
        "median_a": round(stats.median(a) or 0, 4),
        "median_b": round(stats.median(b) or 0, 4),
        "effect_d": round(d, 3) if d is not None else None,
        "effect_label": stats.effect_magnitude(d),
        "cliffs": round(delta, 3) if delta is not None else None,
        "p_unit": round(stats.permutation_test(a, b), 4),
        "n_topics_a": cluster.get("n_a", 0),
        "n_topics_b": cluster.get("n_b", 0),
        "p_topic": cluster.get("p"),
        "stat_topic": cluster.get("stat"),
        "higher_in": higher_in,
        "binary": binary,
        "conditional": conditional,
        "topic_values_a": cluster.get("values_a"),
        "topic_values_b": cluster.get("values_b"),
    }
    # 零膨胀指标真正可读的效应量：发生率之差 / 相对变化
    if binary:
        r["rate_a"] = round(mean_a, 3)
        r["rate_b"] = round(mean_b, 3)
        r["rate_diff"] = round(mean_b - mean_a, 3)
        r["rate_ratio"] = round(mean_b / mean_a, 2) if mean_a else None
    else:
        if mean_a:
            r["rel_change"] = round((mean_b - mean_a) / mean_a, 3)
    return r


def _verdict(r: dict) -> str:
    """判决顺序：先方向，再实质差异，最后显著性。

    方向符号容易搞反，这里写清楚：`cohen_d(a, b)` 里 a=T3、b=T5，
    所以 d>0 表示 **T3 更高**，d<0 表示 **T5 更高**。
    """
    if r.get("status") != "ok":
        return "无法检验（样本不足或字段缺失）"

    # H8 走符号检验，没有 Cohen's d
    if "sign_pos" in r and r.get("sign_pos") is not None:
        pos, neg, p = r["sign_pos"], r["sign_neg"], r.get("p_topic")
        if pos + neg == 0:
            return "无法检验（无有效话题）"
        # H8 的样本量不在 n(T3)/n(T5) 列里，带进判决文本才看得见
        n_min = r.get("n_minority")
        extra = f"，少数派 n={n_min:,}" if isinstance(n_min, int) else ""
        if pos <= neg:
            return f"方向与假设相反（{pos} 个话题支持，{neg} 个相反{extra}）"
        if isinstance(p, (int, float)) and p < config.ALPHA:
            return f"支持假设（{pos}/{pos + neg} 个话题方向一致，符号检验显著{extra}）"
        return f"方向一致但未达显著（{pos}/{pos + neg} 个话题{extra}）"

    hi = r.get("higher_in")

    # 二元指标：看发生率之差（百分点），不用 Cohen's d 判实质差异
    if r.get("binary"):
        diff = float(r.get("rate_diff") or 0.0)      # T5 − T3
        direction_ok = (diff > 0) if hi == "T5" else (diff < 0)
        if abs(diff) < config.MIN_RATE_DIFF:
            return (f"无实质差异（发生率差 {diff:+.1%}，"
                    f"低于 {config.MIN_RATE_DIFF:.0%} 门槛），不支持假设")
        return _by_p(r, direction_ok)

    d = r.get("effect_d")
    if d is None:
        return "无法计算效应量"
    direction_ok = (d > 0) if hi == "T3" else (d < 0)
    rel = r.get("rel_change")
    big = abs(d) >= config.MIN_EFFECT_FOR_CLAIM or (
        rel is not None and abs(rel) >= config.MIN_REL_CHANGE)
    if not big:
        extra = f"，相对变化 {rel:+.0%}" if rel is not None else ""
        return f"无实质差异（|d|={abs(d):.2f}{extra}），不支持假设"
    return _by_p(r, direction_ok)


def _by_p(r: dict, direction_ok: bool) -> str:
    if not direction_ok:
        return "方向与假设相反"
    p_topic = r.get("p_topic")
    if isinstance(p_topic, (int, float)) and p_topic < config.ALPHA:
        return "支持假设（话题级精确检验显著）"
    if r.get("p_unit") is not None and r["p_unit"] < config.ALPHA:
        return "方向一致，但话题级不显著（单元级显著属伪重复，记为弱证据）"
    return "方向一致但未达统计显著"


def run_all(units: pd.DataFrame) -> pd.DataFrame:
    """units 需含 thread_id / unit / topic_type / 各指标列。"""
    rows: list[dict] = []

    def add(code: str, name: str, metric: str, r: dict, note: str = ""):
        rows.append({"code": code, "hypothesis": name, "metric": metric,
                     **r, "verdict": _verdict(r), "note": note})

    if _need(units, ["topic_type", "thread_id"]):
        # 没有话题类型就没有分组，整张表统一记 unavailable，而不是逐条报缺失
        missing = _need(units, ["topic_type", "thread_id"])
        for code, name, metric in _CATALOG:
            rows.append({"code": code, "hypothesis": name, "metric": metric,
                         "status": "unavailable",
                         "missing": "缺 " + ", ".join(missing),
                         "verdict": "无法检验（字段缺失）", "note": ""})
        return pd.DataFrame(rows)

    rep = units[units["unit"] == "reply"].copy()
    if rep.empty:                    # 有些语料全是 argument 单元
        rep = units.copy()
    rep = rep[rep["topic_type"].isin(T3 + T5)]

    # ── H1 攻击发生率（粗放边际）────────────────────────────────
    miss = _need(rep, ["G1_intensity"])
    if miss:
        add("H1", "T5 的发言中出现攻击的比例高于 T3", "G1_intensity(>0)",
            {"status": "unavailable", "missing": miss},
            "G 组为词典下界，见 COLD 金标准检验")
    else:
        add("H1", "T5 的发言中出现攻击的比例高于 T3", "G1_intensity(>0)",
            _cmp(rep, "G1_intensity", higher_in="T5", binary=True),
            "发生率 = G1>0 的单元占比。稀疏指标先看这一层，再看强度")

        # ── H1b 攻击强度（集约边际）────────────────────────────
        add("H1b", "已经发生攻击的发言中，T5 的攻击强度更高", "G1_intensity(条件均值)",
            _cmp(rep, "G1_intensity", higher_in="T5", conditional=True),
            f"条件于 G1>0，样本量 {int((pd.to_numeric(rep['G1_intensity'], errors='coerce') > 0).sum())} 条")

    # ── H2 群体标签化 ──────────────────────────────────────────
    miss = _need(rep, ["G5_group_label"])
    if miss:
        add("H2", "T5 的群体标签化比例高于 T3", "G5_group_label",
            {"status": "unavailable", "missing": miss})
    else:
        add("H2", "T5 的群体标签化比例高于 T3", "G5_group_label",
            _cmp(rep, "G5_group_label", higher_in="T5", binary=True),
            "群体词 + 全称量词同现才计；单提群体不计")

    # ── H3 第二人称 ────────────────────────────────────────────
    miss = _need(rep, ["B9_second_person"])
    if miss:
        add("H3", "T5 出现第二人称（对抗性标记）的比例高于 T3", "B9_second_person(>0)",
            {"status": "unavailable", "missing": miss})
    else:
        add("H3", "T5 出现第二人称（对抗性标记）的比例高于 T3", "B9_second_person(>0)",
            _cmp(rep, "B9_second_person", higher_in="T5", binary=True))

    # ── H4 绝对化 ──────────────────────────────────────────────
    miss = _need(rep, ["D2_absolutism"])
    if miss:
        add("H4", "T5 的绝对化表达比例高于 T3", "D2_absolutism(>0)",
            {"status": "unavailable", "missing": miss})
    else:
        add("H4", "T5 的绝对化表达比例高于 T3", "D2_absolutism(>0)",
            _cmp(rep, "D2_absolutism", higher_in="T5", binary=True))

    # ── H5 反讽 ────────────────────────────────────────────────
    miss = _need(rep, ["G7_irony"])
    if miss:
        add("H5", "T5 的反讽标记出现率高于 T3", "G7_irony(>0)",
            {"status": "unavailable", "missing": miss})
    else:
        add("H5", "T5 的反讽标记出现率高于 T3", "G7_irony(>0)",
            _cmp(rep, "G7_irony", higher_in="T5", binary=True),
            "反讽是本项目已知的主要漏检来源：褒词反用在通用词典里被判为正面")

    # ── H6 篇幅 ────────────────────────────────────────────────
    miss = _need(rep, ["B0_len"])
    if miss:
        add("H6", "制度指向（T3）的发言长于行动者指向（T5）", "B0_len",
            {"status": "unavailable", "missing": miss})
    else:
        add("H6", "制度指向（T3）的发言长于行动者指向（T5）", "B0_len",
            _cmp(rep, "B0_len", higher_in="T3"),
            "长度是所有密度类指标的分母，组间长度差异会传导到其他指标")

    # ── H7 攻击对象 ────────────────────────────────────────────
    miss = _need(rep, ["G2_target"])
    if miss:
        add("H7", "T5 中攻击对象为人/群体的比例高于 T3", "G2_target",
            {"status": "unavailable", "missing": miss})
    else:
        sub = rep[rep["G2_target"].isin(["person", "group", "opinion", "none"])].copy()
        sub["is_person"] = sub["G2_target"].isin(["person", "group"]).astype(float)
        add("H7", "T5 中攻击对象为人/群体的比例高于 T3", "G2_target",
            _cmp(sub, "is_person", higher_in="T5", binary=True),
            "G2 依赖词典命中，漏检的恰恰是无脏词的群体攻击")

    # ── H8 少数派更激进 ───────────────────────────────────────
    # 这条是话题内比较，天然控制了话题构成，是本次最容易站住的一条。
    if _need(rep, ["D6_stance", "G1_intensity"]):
        add("H8", "与话题主流立场相反者，攻击强度更高", "G1_intensity",
            {"status": "unavailable",
             "missing": _need(rep, ["D6_stance", "G1_intensity"])},
            "需要数据集自带立场标签；且该标签为机器生成，见局限")
    else:
        add("H8", "与话题主流立场相反者，攻击强度更高", "G1_intensity",
            _minority_test(rep),
            "话题内比较：每个话题以其多数立场为基准，避免话题构成混杂")

    return pd.DataFrame(rows)


_CATALOG = [
    ("H1", "T5 的发言中出现攻击的比例高于 T3", "G1_intensity(>0)"),
    ("H1b", "已经发生攻击的发言中，T5 的攻击强度更高", "G1_intensity(条件均值)"),
    ("H2", "T5 的群体标签化比例高于 T3", "G5_group_label"),
    ("H3", "T5 出现第二人称（对抗性标记）的比例高于 T3", "B9_second_person(>0)"),
    ("H4", "T5 的绝对化表达比例高于 T3", "D2_absolutism(>0)"),
    ("H5", "T5 的反讽标记出现率高于 T3", "G7_irony(>0)"),
    ("H6", "制度指向（T3）的发言长于行动者指向（T5）", "B0_len"),
    ("H7", "T5 中攻击对象为人/群体的比例高于 T3", "G2_target"),
    ("H8", "与话题主流立场相反者，攻击强度更高", "G1_intensity"),
]


def _minority_test(rep: pd.DataFrame) -> dict:
    """H8：话题内多数派 vs 少数派。

    每个话题单独定多数立场，再看少数派的**攻击发生率**是否更高。
    用发生率而不用强度中位数：G1 在 94% 的单元上是 0，
    两边的中位数都是 0，符号检验会得到「0 个支持、0 个相反」。
    话题级检验用符号检验：多少个话题方向一致。
    """
    rows = []
    for tid, g in rep.groupby("thread_id"):
        vc = g["D6_stance"].value_counts()
        vc = vc[vc.index.isin(["pro", "con"])]
        if len(vc) < 2 or vc.sum() < 30:
            continue
        major = vc.idxmax()
        g1 = pd.to_numeric(g["G1_intensity"], errors="coerce")
        maj_mask = g["D6_stance"] == major
        maj = (g1[maj_mask] > 0).astype(float).dropna()
        mino = (g1[~maj_mask] > 0).astype(float).dropna()
        if len(mino) < 10 or len(maj) < 10:
            continue
        rows.append({
            "topic": tid, "n_minority": int(len(mino)),
            "rate_minority": float(mino.mean()),
            "rate_majority": float(maj.mean()),
            "diff": float(mino.mean() - maj.mean()),
        })
    if not rows:
        return {"status": "insufficient_topics",
                "detail": "没有话题同时具备两种立场且各 ≥10 条"}
    t = pd.DataFrame(rows)
    s = stats.sign_test(t["diff"].tolist())
    return {
        "status": "ok",
        "n_topics": len(t),
        "rate_a": round(float(t["rate_minority"].mean()), 3),
        "rate_b": round(float(t["rate_majority"].mean()), 3),
        "rate_diff": round(float(t["rate_minority"].mean()
                                 - t["rate_majority"].mean()), 3),
        "n_minority": int(t["n_minority"].sum()),
        # H8 不分 T3/T5，塞进 n(T3) 列会被读成「T3 有 1101 条」
        "n_a": None,
        "n_b": None,
        "sign_pos": s.get("pos"), "sign_neg": s.get("neg"),
        "p_topic": s.get("p"), "p_unit": None,
        "stat_topic": round(float(t["diff"].median()), 4),
        "higher_in": "minority",
        "binary": True,
        "topic_values_a": t["diff"].round(3).tolist(),
        "detail": "；".join(f"{r.topic} {r.diff:+.1%}" for r in t.itertuples()),
    }
