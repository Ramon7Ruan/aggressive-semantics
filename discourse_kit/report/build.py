"""报告生成（情形 A：结构化分析报告）。

章节顺序是刻意的，不是排版偏好：

0. 概览
1. **语料质量** —— 先讲这份语料能被测量到什么程度。句法类指标在去标点的
   语料上会「有值但无意义」，读者必须先知道这件事，才有资格读后面的数字。
2. 指标可用性台账 —— 哪些算了、哪些没算、为什么。
3. 话题编码 —— 自变量是怎么定义的，逐话题给出依据。
4. 分组描述统计
5. 假设检验 —— 话题级精确检验为主，单元级 p 仅作参考。
6. 局限

把「哪些没算、为什么」放在结论之前，报告才具备可质疑性。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .. import config, stats
from ..metrics.registry import REGISTRY
from ..normalize import tokenizer_name
from ..schema import UNIT_OPENING, UNIT_REPLY


def _cell(v: object) -> str:
    """单元格值转义。

    竖线必须转义：判决文本里有 `|d|=0.16` 这样的写法，不转义会把该行拆成
    多列，markdown 表格会整体错位——而且是「看起来还在、内容错位」的错，
    比报错更难发现。换行同样会打断表格，压成空格。
    """
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _md_table(df: pd.DataFrame, *, floatfmt: int = 3) -> str:
    if df.empty:
        return "_（无数据）_"
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else f"{x:.{floatfmt}f}")
        else:
            d[c] = d[c].fillna("").astype(str)
    head = "| " + " | ".join(_cell(c) for c in d.columns) + " |"
    sep = "|" + "|".join("---" for _ in d.columns) + "|"
    body = "\n".join("| " + " | ".join(_cell(v) for v in row) + " |"
                     for row in d.itertuples(index=False))
    return "\n".join([head, sep, body])


# ── 1 语料质量 ────────────────────────────────────────────────────

def _section_quality(corpus, n_topics: int) -> str:
    lines = ["## 1. 语料质量：这份数据能测什么", ""]
    if corpus is None or not getattr(corpus, "n", 0):
        return "\n".join(lines + ["_（未计算语料画像）_"])

    lines.append(_md_table(pd.DataFrame([
        ("分析单元", f"{corpus.n:,}"),
        ("话题数", f"{n_topics}"),
        ("字符数中位", f"{corpus.median_chars:.0f}"),
        ("字符数均值", f"{corpus.mean_chars:.0f}"),
        ("P90 字符数", f"{corpus.p90_chars:.0f}"),
        ("≤10 字占比", f"{corpus.short_share_10:.1%}"),
        ("含标点占比", f"{corpus.punctuation_rate:.1%}"),
        ("含句末标点占比", f"{corpus.sentence_end_rate:.1%}"),
        ("含换行占比", f"{corpus.paragraph_rate:.1%}"),
    ], columns=["项目", "取值"])))
    lines.append("")

    from ..quality import warnings as qwarnings
    warns = qwarnings(corpus)
    if warns:
        # 数量写死过，结果只返回 2 条却印「三条」——改成跟着条数走
        lines.append(f"**这份语料的 {len(warns)} 条硬约束：**")
        lines.append("")
        for w in warns:
            lines.append(f"- {w}")
    else:
        lines.append("语料形态正常：标点与段落齐全，句法类指标可用。")
    lines.append("")
    lines.append("> 判定规则：含句末标点的单元不足 50% 时，分句退化为「整段一句」，"
                 "此时句长、论证密度、问句占比一类指标**不是有偏，而是失效**，"
                 "已整列弃用（见第 2 章 `invalid_for_corpus`）。")
    return "\n".join(lines)


# ── 2 台账 ────────────────────────────────────────────────────────

def _section_ledger(ledger: pd.DataFrame) -> str:
    lines = ["## 2. 指标可用性台账", "",
             "本表说明每个指标是否算得出、因何未算、覆盖多少样本。",
             "**未列入本表的指标不应在本报告中出现；本表中的 `skipped` 不得在结论里被当作 0。**", ""]
    if ledger.empty:
        return "\n".join(lines + ["_（无）_"])
    cols = [c for c in ("key", "group", "name", "status", "n_ok", "coverage", "warning")
            if c in ledger.columns]
    show = ledger[cols].copy()
    if "coverage" in show:
        show["coverage"] = (show["coverage"] * 100).round(0).astype(int).astype(str) + "%"
    lines.append(_md_table(show))

    bad = ledger[ledger["status"] != "ok"]
    if not bad.empty:
        lines.append("")
        lines.append(f"**{len(bad)} 个指标未产出有效值：**")
        for _, r in bad.iterrows():
            lines.append(f"- `{r['key']}` {r['name']}｜{r['status']}：{r['detail']}")
    if "warning" in ledger.columns:
        low = ledger[(ledger["status"] == "ok") & (ledger["warning"].fillna("") != "")]
        if not low.empty:
            lines.append("")
            lines.append(f"**{len(low)} 个指标虽可算但覆盖率过低，不宜据此下结论：**"
                         + "、".join(f"`{k}`" for k in low["key"]))
    return "\n".join(lines)


# ── 3 话题编码 ────────────────────────────────────────────────────

def _section_topics(labels: pd.DataFrame | None, topic_table=None) -> str:
    lines = ["## 3. 话题编码（自变量定义）", ""]
    if topic_table is not None and getattr(topic_table, "topics", None):
        rows = []
        for t in topic_table.topics.values():
            rows.append({
                "话题": t.topic_id, "编码": t.code, "子型": t.subtype,
                "置信度": t.confidence, "争论焦点": t.focus, "判定依据": t.rationale,
            })
        df = pd.DataFrame(rows).sort_values(["编码", "话题"])
        lines.append("**话题类型为给定分组，由人工逐话题判定**（规则标注器看不到主楼，"
                     "只能看标题，标题又常是省略句，所以不交给它猜）。")
        lines.append("")
        lines.append(_md_table(df))
        vc = df["编码"].value_counts().rename_axis("类型").reset_index(name="话题数")
        lines.append("")
        lines.append("分组规模：" + "，".join(f"{r['类型']} {r['话题数']} 个"
                                        for _, r in vc.iterrows()) + "。")
        low = df[df["置信度"] == "medium"]
        if not low.empty:
            lines.append("")
            lines.append(f"其中 {', '.join(low['话题'])} 的焦点判为制度而非行动者，"
                         "置信度标 medium。这两例属于可争议编码，"
                         "结论不应只依赖它们——见第 5 章末尾的敏感性说明。")
        return "\n".join(lines)

    if labels is None or labels.empty:
        return "\n".join(lines + ["_（未做话题编码，本章跳过）_"])
    tt = labels[labels["dimension"] == "topic_type"]
    if tt.empty:
        return "\n".join(lines + ["_（无 topic_type 标注）_"])
    vc = tt["label"].value_counts().rename_axis("类型").reset_index(name="数量")
    lines.append(_md_table(vc))
    unk = tt[tt["label"] == "T?"]
    if len(unk):
        lines.append("")
        lines.append(f"其中 {len(unk)} 条无法归入五类（T?）。"
                     "占比过高说明分类体系与实际语料不匹配，应先修订手册，不应带病进入检验。")
    return "\n".join(lines)


# ── 4 分组描述统计 ────────────────────────────────────────────────

_GROUP_STATS = ("B0_len", "G1_intensity", "G5_group_label", "B9_second_person",
                "D2_absolutism", "G7_irony", "D1_polarity", "B10_slang_density")

# 报告显示用：把指标列变成「发生率」或「均值」，并给中文列名。
# 为什么这里一律用**均值**而不是中位数：G1/G5/G7 只有个位数百分比非零，
# 中位数恒为 0，一张全是 0 的表既看不出差异，也和第 5 章用的均值对不上。
_SHOW = [
    ("B0_len", "平均字数", "mean"),
    ("G1_intensity", "攻击发生率", "rate"),
    ("G5_group_label", "群体标签化率", "rate"),
    ("B9_second_person", "第二人称出现率", "rate"),
    ("D2_absolutism", "绝对化出现率", "rate"),
    ("G7_irony", "反讽出现率", "rate"),
]


def _aggregate(df: pd.DataFrame, keys: tuple[str, str, str] | tuple[str, str]):
    """按 keys 分组，把 _SHOW 里的指标聚合成一行。"""
    out = []
    for key, label, how in _SHOW:
        if key not in df.columns:
            continue
        s = pd.to_numeric(df[key], errors="coerce")
        if how == "rate":
            s = (s > 0).astype(float)
        g = s.groupby([df[k] for k in keys]).mean()
        g.name = label
        out.append(g)
    if not out:
        return pd.DataFrame()
    res = pd.concat(out, axis=1)
    res["n"] = df.groupby(list(keys)).size()
    return res.reset_index()


def _section_group_stats(units_df: pd.DataFrame, metrics: pd.DataFrame) -> str:
    lines = ["## 4. 分组描述统计", ""]
    if metrics.empty:
        return "\n".join(lines + ["_（无指标表，本章跳过）_"])
    if "topic_type" not in units_df.columns:
        return "\n".join(lines + ["_（无话题标注，本章跳过）_"])

    base = units_df[["doc_id", "unit", "thread_id", "topic_type"]].copy()
    df = pd.concat([base.set_index("doc_id"), metrics], axis=1)
    rep = df[(df["unit"] == UNIT_REPLY) & df["topic_type"].notna()]
    if rep.empty:
        return "\n".join(lines + ["_（无带话题标注的回复单元）_"])

    lines.append("「发生率」= 该指标大于 0 的单元占比；「平均字数」为均值。"
                 "所有列都用均值口径，与第 5 章检验统计量一致。")
    lines.append("")
    g = _aggregate(rep, ("topic_type",))
    if not g.empty:
        g = g.rename(columns={"topic_type": "话题类型"}).sort_values("话题类型")
        lines.append(_md_table(g))

    # 话题级明细：16 个点全部列出，读者可自行判断组间差异是否被个别话题驱动
    lines.append("")
    lines.append("### 4.1 话题级明细")
    lines.append("")
    lines.append(f"共 {rep['thread_id'].nunique()} 行，每行一个话题。"
                 "假设检验的推断单位是这些行，不是评论——"
                 "所以这里逐点列出，方便判断组间差异是否由一两个话题带动。")
    lines.append("")
    t = _aggregate(rep, ("thread_id", "topic_type"))
    if not t.empty:
        t = (t.rename(columns={"thread_id": "话题", "topic_type": "类型"})
             .sort_values(["类型", "攻击发生率"], ascending=[True, False]))
        lines.append(_md_table(t))

    # 方差分解：先看「话题」解释了多大比例，再看「类型」解释了多少
    if "G1_intensity" in rep.columns:
        g1 = pd.to_numeric(rep["G1_intensity"], errors="coerce")
        by_topic = {k: v.dropna().tolist()
                    for k, v in g1.groupby(rep["thread_id"])}
        by_type = {k: v.dropna().tolist()
                   for k, v in g1.groupby(rep["topic_type"])}
        icc_topic = stats.icc_oneway(by_topic)
        icc_type = stats.icc_oneway(by_type)
        if icc_topic and icc_type:
            lines.append("")
            lines.append("### 4.2 方差分解：话题还是类型在解释差异？")
            lines.append("")
            lines.append(_md_table(pd.DataFrame([
                ("按话题分组（16 组）", f"{icc_topic['icc']:.3f}"),
                ("按类型分组（2 组：T3/T5）", f"{icc_type['icc']:.3f}"),
                ("总观测数", f"{icc_topic['n_obs']:,}"),
            ], columns=["分组方式", "组间方差占比 ICC"])))
            lines.append("")
            lines.append(
                "读法：ICC 是组间方差占总方差的比例。**先看话题这一行**——"
                "它回答「同一话题内部的发言是否比跨话题更相似」。"
                "再看类型这一行：如果话题的 ICC 明显高于类型，"
                "说明真正起作用的是具体议题（谁、什么事），而不是我们划的类型，"
                "「类型不影响话语形态」这种结论就不能下。")
    return "\n".join(lines)


# ── 5 假设检验 ────────────────────────────────────────────────────

_HYP_COLS = ("code", "hypothesis", "metric", "n_a", "n_b",
             "mean_a", "mean_b", "effect_d", "effect_label",
             "p_unit", "p_topic", "verdict")


def _section_hyp(hyp: pd.DataFrame) -> str:
    lines = ["## 5. 假设检验", ""]
    if hyp.empty:
        return "\n".join(lines + ["_（未运行）_"])
    cols = [c for c in _HYP_COLS if c in hyp.columns]
    show = hyp[cols].copy()
    show = show.rename(columns={
        "mean_a": "均值(T3)", "mean_b": "均值(T5)", "effect_d": "d",
        "effect_label": "效应量", "p_unit": "p(单元)", "p_topic": "p(话题)",
        "n_a": "n(T3)", "n_b": "n(T5)"})
    # 计数列显示成整数：混进 NaN 后会变成 9120.000，读起来像连续变量
    for c in ("n(T3)", "n(T5)"):
        if c in show:
            show[c] = (pd.to_numeric(show[c], errors="coerce")
                       .astype("Int64").astype(str).replace("<NA>", ""))
    lines.append(_md_table(show))
    lines.append("")
    lines.append("「均值」列对二分口径的指标（标 `(>0)`）即发生率；"
                 "`(条件均值)` 只统计已发生的发言，回答「说得多狠」。两者与第 4 章口径一致。")

    lines.append("")
    lines.append("**读法（三条，缺一条都会误读）：**")
    lines.append("")
    lines.append("1. **`p(话题)` 才是可引用的 p。** 16 个话题、6 vs 10 的分组，"
                 "穷举全部 8008 种分组得到精确 p，不依赖分布假设。")
    lines.append("2. **`p(单元)` 只作参考。** 把 14,705 条评论当独立样本会把微小差异推成「显著」，"
                 "这是伪重复（pseudoreplication）；它显著而 `p(话题)` 不显著时，结论按后者写。")
    lines.append("3. **|d| < 0.2 不写成「更倾向于」。** 效应量门槛先于 p 值生效，"
                 "大样本下 p 很容易小，但小效应没有解释价值。")

    ok = hyp[(hyp["status"] == "ok")] if "status" in hyp else pd.DataFrame()
    if not ok.empty:
        lines.append("")
        lines.append("### 5.1 方向一致性")
        lines.append("")
        for _, r in ok.iterrows():
            va = r.get("topic_values_a")
            vb = r.get("topic_values_b")
            if not isinstance(va, list) or not isinstance(vb, list):
                continue
            lines.append(
                f"- **{r['code']}** `{r.get('metric','')}`："
                f"T3 话题值 {va}｜T5 话题值 {vb}")
        lines.append("")
        lines.append("若两组取值区间大面积重叠，即使 p 显著也只说明分布整体平移，"
                     "不代表每个话题都如此——这正是话题级明细要逐点列出的原因。")
    return "\n".join(lines)


# ── 6 局限 ────────────────────────────────────────────────────────

def _g_eval_note() -> str:
    """从 COLD 金标准的评估结果里取数字，避免报告里硬编码过期数值。

    没跑过评估时明确说明「未评估」，而不是拿一句「可能偏低」搪塞。
    """
    import json

    p = config.REPORT_DIR / "cold_eval.json"
    if not p.exists():
        return ("尚未在人工金标准上评估（先跑 `python scripts/eval_cold.py`）。"
                "**在评估完成前，G 组的绝对值不得用于任何比较。**")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "金标准评估结果无法解析，同上前提：G 组绝对值不可用于比较。"

    det = d.get("detail") or {}
    g = det.get("攻击群体") or {}
    p_ = det.get("攻击个人") or {}
    parts = [
        f"在 COLD 人工标注（n={d.get('n', 0):,}）上，G1>0 判为冒犯的"
        f"精确率 {d.get('precision', 0):.3f}、召回率 {d.get('recall', 0):.3f}、"
        f"AUC {d.get('auc', 0):.3f}，漏检 {d.get('miss_rate', 0):.1%}。"
    ]
    if g and p_:
        parts.append(
            f"按对象拆：攻击个人检出 {p_.get('rate', 0):.1%}，"
            f"攻击群体仅 {g.get('rate', 0):.1%}——"
            "群体攻击多用「某地人都这样」这类无脏词表述，词表覆盖不到。")
    parts.append("**结论：组间差异可读，绝对值不可读；且偏差方向为系统性低估、群体向更严重。**")
    return "".join(parts)


def _section_limits(info: dict, corpus=None) -> str:
    items = [
        f"**数据来源**：{info.get('source_note', '公开数据集')}。"
        "话题选择与抽样规则由原作者决定，本研究不能声称代表性。",
        "**人群偏差**：微博用户结构与一般公众不同，结论只能限定在该平台语境。",
        "**G 组是下界**：" + _g_eval_note(),
        "**反讽漏检**：褒词反用（「睿智」「典」）在通用词典里被判为正面，"
        "这是已知的方向性偏差，不是随机噪声。",
        f"**分词器**：{tokenizer_name()}。词汇类指标依赖切分粒度，跨切分器不可直接比较。",
    ]
    if corpus is not None and getattr(corpus, "n", 0):
        if corpus.sentence_end_rate < 0.5:
            items.append(
                f"**语料被去标点**（含句末标点 {corpus.sentence_end_rate:.1%}）："
                "句法、论证结构类指标在本语料上不可用，已整列弃用。"
                "本报告因此**不能**就「论证方式」作任何结论——这是数据限制，不是分析选择。")
        if corpus.median_chars < 20:
            items.append(
                f"**单元极短**（中位 {corpus.median_chars:.0f} 字）："
                "词表类指标的抽样波动很大，密度类指标在 10 字文本上主要由分母驱动。")
    items.append(
        "**话题类型为人工编码**：编码规则与依据全部公开（第 3 章），"
        "但单编码者无评分者信度（κ），这是本研究最薄弱的一环。")
    items.append(
        "**立场标签为机器生成**（数据集 `annotations_creators: machine-generated`），"
        "仅用于同话题内多数/少数派划分，不作为人工金标准。")

    lines = ["## 6. 局限", ""]
    for i, s in enumerate(items, 1):
        lines.append(f"{i}. {s}")
    return "\n".join(lines)


# ── 组装 ──────────────────────────────────────────────────────────

def build(out_path: str | Path, *, info: dict, units_df: pd.DataFrame,
          metrics: pd.DataFrame, threads: pd.DataFrame, ledger: pd.DataFrame,
          hyp: pd.DataFrame, labels: pd.DataFrame | None = None,
          corpus=None, topic_table=None) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    n_topics = units_df["thread_id"].nunique()
    parts = [
        "# 社会议题讨论中的话语形态：制度指向 vs 行动者指向",
        "",
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ "
        f"schema v1 ｜ 指标 {len(REGISTRY)} 个 ｜ 分析单元 {len(units_df):,} ｜ 话题 {n_topics}",
        "",
        "## 0. 研究问题与数据",
        "",
        "**研究问题**：在社会议题讨论中，指向议题本身（制度/政策）的发言与"
        "指向不在场第三方（个人/群体/机构/国家）的发言，在语言形态与攻击性上有何差异？",
        "",
        f"- 数据来源：{info.get('source_note', '—')}",
        f"- 分析单元：{len(units_df):,} 条，来自 {n_topics} 个话题",
        f"- 分组：T3 制度指向 / T5 行动者指向（人工编码，逐话题依据见第 3 章）",
        f"- 推断单位：**话题**（不是评论），话题级精确置换检验",
        f"- 文本导出模式：{info.get('text_mode', 'truncate')}",
        "",
        _section_quality(corpus, n_topics),
        "",
        _section_ledger(ledger),
        "",
        _section_topics(labels, topic_table),
        "",
        _section_group_stats(units_df, metrics),
        "",
        _section_hyp(hyp),
        "",
        _section_limits(info, corpus),
        "",
        "---",
        "",
        f"配置：MATTR 窗口 {config.MATTR_WINDOW}；置换检验 {config.PERMUTATION_N} 次；"
        f"bootstrap {config.BOOTSTRAP_N} 次；效应量门槛 |d| ≥ {config.MIN_EFFECT_FOR_CLAIM}；"
        f"显著性水平 α = {config.ALPHA}。",
    ]
    p.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return p
