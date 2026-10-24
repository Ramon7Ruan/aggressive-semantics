#!/usr/bin/env python
"""用 COLD 的人工标注检验我们的 G 组攻击性指标。

为什么必须做这件事：G1 是词典法算出来的，而中文社区的攻击大量以
「褒义词反用」出现（睿智/典/孝/急了），词典会把它们判为正面。
如果不量化这个偏差，报告里就只能写「G1 可能偏低」这种没有信息量的话。

用法（先跑过 COLD 管线）：

    ./bin/dk prepare --dataset cold --path data/raw/cold --out data/raw/prepared/cold.jsonl
    ./bin/dk run --file data/raw/prepared/cold.jsonl --out data/processed/cold
    python scripts/eval_cold.py

输出：混淆矩阵、precision/recall/F1/AUC，以及按攻击对象（个人/群体）拆分的检出率。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
METRICS = ROOT / "data" / "processed" / "cold" / "metrics.csv"
RAW = ROOT / "data" / "raw" / "cold"


def auc_rank(s: np.ndarray, y: np.ndarray) -> float:
    """秩和法算 AUC，避免为此引入 sklearn。并列值取平均秩。"""
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    ranks[order] = np.arange(1, len(s) + 1)
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    avg = np.bincount(inv, weights=ranks) / cnt
    ranks = avg[inv]
    n1 = int((y == 1).sum())
    n0 = int((y == 0).sum())
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def load_gold() -> pd.DataFrame:
    rows = []
    for split in ("train", "dev", "test"):
        f = RAW / f"{split}.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f, encoding="utf-8-sig")
        for i, r in df.iterrows():
            fine = -1
            if "fine-grained-label" in df and pd.notna(r.get("fine-grained-label")):
                fine = int(r["fine-grained-label"])
            rows.append({"doc_id": f"cold-{split}-{i}",
                         "y": int(r["label"]), "fine": fine})
    return pd.DataFrame(rows).set_index("doc_id")


def main() -> int:
    if not METRICS.exists():
        print(f"找不到 {METRICS}；先跑 COLD 管线。", file=sys.stderr)
        return 1
    m = pd.read_csv(METRICS, index_col=0)
    df = m.join(load_gold(), how="inner")
    if df.empty:
        print("指标与金标准没有交集，检查 doc_id 规则是否改过。", file=sys.stderr)
        return 1

    y = df["y"].to_numpy()
    s = df["G1_intensity"].to_numpy(float)
    pred = (s > 0).astype(int)

    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)

    print(f"样本 {len(df)} 条（与金标准匹配后）")
    print(f"G1>0 当预测：precision={prec:.3f} recall={rec:.3f} "
          f"F1={f1:.3f} AUC={auc_rank(s, y):.3f}")
    print(f"混淆：TP={tp} FP={fp} FN={fn} TN={tn}")
    n_pos = int((y == 1).sum())
    print(f"金标准冒犯占比 {n_pos / len(y):.1%}，G1>0 占比 {pred.mean():.1%}")
    print(f"漏检 {fn} / {n_pos} = {fn / max(n_pos, 1):.1%}")

    print("\n按攻击对象拆分（仅 test 有细分标注）：")
    detail = {}
    for k, name in [(1, "攻击个人"), (2, "攻击群体"), (0, "安全(其他)"), (3, "反偏见")]:
        sub = df[df["fine"] == k]
        if len(sub):
            rate = float((sub["G1_intensity"] > 0).mean())
            lab = "检出率" if k in (1, 2) else "误报率"
            detail[name] = {"n": int(len(sub)), "rate": round(rate, 4),
                            "kind": "recall" if k in (1, 2) else "false_positive"}
            print(f"  {name:8s} n={len(sub):5d}  {lab}={rate:.1%}")

    print("\nG1 在人工标签下的分布：")
    print(df.groupby("y")["G1_intensity"].agg(["mean", "median", "count"]).round(2).to_string())

    # 落盘：报告第 6 章要引用这些数字。写成 JSON 而不是让报告硬编码，
    # 否则词表一改，报告里的数字就会过期且无人察觉。
    import json

    out = ROOT / "report" / "cold_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n": int(len(df)),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "auc": round(auc_rank(s, y), 4),
        "miss_rate": round(fn / max(n_pos, 1), 4),
        "pred_rate": round(float(pred.mean()), 4),
        "gold_rate": round(n_pos / len(y), 4),
        "detail": detail,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
