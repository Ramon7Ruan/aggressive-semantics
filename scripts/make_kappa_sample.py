"""抽双编码样本：200 条，分两层，按话题配额。

为什么分层而不是纯随机：
G1>0 在 weibo16 只有 4% 左右。纯随机抽 200 条，阳性大约 8 条，
两名编码者只要把这 8 条全判成 none，κ 也会接近 1——这是 kappa paradox，
衡量的是「都不认为是攻击」而不是「判断标准是否一致」。所以阳性/阴性各抽一半。

为什么按话题配额：
16 个话题的攻击发生率差 50 倍（韩国吃播道歉 14.8%，外国人 0.3%）。
不按话题配额，样本会被高发话题占满，κ 只在少数话题上有效。

输出：
  data/annotation/kappa_sample.jsonl   机器用（含全文）
  data/annotation/annotate.html        人工用（单文件，离线，内嵌样本）
两份都不入仓（含原文），只留生成脚本与模板。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from discourse_kit import config  # noqa: E402

OUT_DIR = BASE / "data/annotation"
TEMPLATE = BASE / "tools/annotate.html"


def _full_texts(prepared: Path) -> dict[str, str]:
    """doc_id → 全文。

    不能直接从 units.csv 取：那份是 --text-mode truncate 导出的，
    编码者看到的是被截断的句子，而「有没有攻击」恰恰常在句尾。
    """
    out: dict[str, str] = {}
    with prepared.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[str(r["doc_id"])] = str(r.get("text") or "")
    return out


def _stratified(units: pd.DataFrame, metrics: pd.DataFrame,
                per_topic_pos: int, per_topic_neg: int, per_topic_group: int,
                seed: int) -> tuple[pd.DataFrame, list[str]]:
    """三层抽样：群体指称 / 其它阳性 / 阴性。

    第三层是必须的。词表在本语料上只判出 8 条 group（占 0.05%），
    而人工一眼就能看出「韩国人都这样」是群体攻击。若不主动把
    含群体指称的文本抽进来，人工标出的 group 会没有足够样本，
    κ 在 group 这一类上的估计会宽到没有意义。
    """
    df = pd.DataFrame({
        "doc_id": units["doc_id"].astype(str),
        "thread_id": units["thread_id"].astype(str),
        "G1": pd.to_numeric(metrics["G1_intensity"], errors="coerce"),
        "G2": metrics["G2_target"].astype(str),
        "G5": pd.to_numeric(metrics.get("G5_group_label"), errors="coerce"),
    })
    df["G5"] = df["G5"].fillna(0)
    rng = random.Random(seed)
    # 记 doc_id 而不是 DataFrame 的 index：排重时拿 index 去和 doc_id 比，
    # isin 永远不匹配，同一条会被上面几层反复抽中
    picked: list[str] = []
    layer: dict[str, str] = {}
    notes: list[str] = []

    def take(pool: pd.DataFrame, k: int, tag: str, tid: str,
             tag_en: str) -> None:
        pool = pool[~pool["doc_id"].isin(picked)]
        docs = list(pool["doc_id"])
        rng.shuffle(docs)
        n = min(k, len(docs))
        if n < k:
            notes.append(f"{tid} {tag}不足：需 {k}，实得 {n}")
        for d in docs[:n]:
            picked.append(d)
            layer[d] = tag_en

    for tid, g in df.groupby("thread_id", sort=True):
        pos = g[g["G1"] > 0]
        neg = g[~g["G1"].fillna(0).gt(0)]
        grp = g[g["G5"] > 0]          # 群体指称候选，优先
        take(grp, per_topic_group, "群体候选", tid, "group")
        take(pos, per_topic_pos, "阳性", tid, "pos")
        take(neg, per_topic_neg, "阴性", tid, "neg")

    order = {d: i for i, d in enumerate(picked)}
    out = df[df["doc_id"].isin(order)].copy()
    out["layer"] = out["doc_id"].map(layer)
    out = (out.assign(_o=out["doc_id"].map(order))
              .sort_values("_o").drop(columns="_o").reset_index(drop=True))
    return out, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default="data/processed/weibo16/units.csv")
    ap.add_argument("--metrics", default="data/processed/weibo16/metrics.csv")
    ap.add_argument("--prepared", default="data/raw/prepared/weibo16.jsonl")
    ap.add_argument("--per-topic-pos", type=int, default=7)
    ap.add_argument("--per-topic-neg", type=int, default=6)
    ap.add_argument("--per-topic-group", type=int, default=4)
    ap.add_argument("--seed", type=int, default=20261021)
    args = ap.parse_args()

    units_p, metrics_p, prep_p = (BASE / args.units, BASE / args.metrics,
                                  BASE / args.prepared)
    for p in (units_p, metrics_p, prep_p):
        if not p.exists():
            print(f"缺文件：{p.relative_to(BASE)}")
            return 1

    units = pd.read_csv(units_p, encoding="utf-8-sig", dtype=str)
    metrics = pd.read_csv(metrics_p, encoding="utf-8-sig")
    if len(units) != len(metrics):
        print(f"行数不一致：units {len(units)} vs metrics {len(metrics)}")
        return 1

    sample, notes = _stratified(units, metrics, args.per_topic_pos,
                               args.per_topic_neg, args.per_topic_group,
                               args.seed)
    texts = _full_texts(prep_p)

    # 只保留长度 ≥ 3 的（与 G2 的 min_chars 一致），否则编码者无法判断
    rows, dropped = [], 0
    for r in sample.itertuples(index=False):
        t = texts.get(r.doc_id, "")
        if len(t.strip()) < 3:
            dropped += 1
            continue
        rows.append({
            "id": len(rows) + 1,
            "doc_id": r.doc_id,
            "thread_id": r.thread_id,
            "topic": r.thread_id.replace("weibo16-", ""),
            # 词表结果只给脚本做对照，不进标注界面，避免锚定编码者
            "_layer": getattr(r, "layer", ""),
            "_g1_word": None if pd.isna(r.G1) else round(float(r.G1), 3),
            "_g2_word": r.G2,
            "text": t,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jl = OUT_DIR / "kappa_sample.jsonl"
    with jl.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 生成离线标注页
    if TEMPLATE.exists():
        html = TEMPLATE.read_text(encoding="utf-8")
        payload = [{"id": r["id"], "topic": r["topic"], "text": r["text"]}
                   for r in rows]
        html = html.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
        (OUT_DIR / "annotate.html").write_text(html, encoding="utf-8")
        page = "data/annotation/annotate.html"
    else:
        page = "（缺 tools/annotate.html 模板，未生成标注页）"

    n_pos = sum(1 for r in rows if r["_g1_word"])
    lay = pd.Series([r["_layer"] for r in rows]).value_counts().to_dict()
    print(f"样本 {len(rows)} 条（阳性 {n_pos} / 阴性 {len(rows) - n_pos}）"
          f"，话题 {len({r['thread_id'] for r in rows})} 个")
    print("  分层：" + "，".join(
        f"{k}={v}" for k, v in sorted(lay.items())))
    if dropped:
        print(f"  跳过过短文本 {dropped} 条")
    for n in notes:
        print(f"  [配额外] {n}")
    print(f"  → {jl.relative_to(BASE)}")
    print(f"  → {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
