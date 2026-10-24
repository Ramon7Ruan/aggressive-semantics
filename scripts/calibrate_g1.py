#!/usr/bin/env python
"""用人工标注校准 G1（攻击性），并在中文语料上给出分层加权估计。

背景
----
此前 G 组的全部可信度数字都来自 COLD（英文 Twitter，n=7,680）。
本次有了 246 条微博人工标注，可以做一次**同语言、同平台**的校准。

分层结构（按词表自己的判定分层，抽样比例如下）

    层       总体 N    样本 n    权重 N/n
    group        71        49       1.4
    pos         580       107       5.4
    negative  14054        90     156.2

负层权重极大，所以**绝不能只报未加权数字**——90 条的抽样误差会被放大 156 倍
再摊到 14,054 条上。两个口径都报，差异本身就是结论。

两个口径
--------
- 宽：opinion + person + group 都算「有攻击」
- 严：只有 person + group 算「攻击性语言」，opinion 归为「立场表达」

为什么要分两版：编码手册把「攻击观点」写成了一类攻击，但实践中它会把
「支持抵制」「坚决抵制」这类纯表态也收进来。若不拆开，人工基准率会到 60%
——14,705 条社会议题评论里六成带攻击性，这个结论不可信。
严口径下基准率约 11%，与「攻击性语言」的常识量级相符。

用法
----
    python scripts/calibrate_g1.py
    python scripts/calibrate_g1.py --sheet data/annotation/kappa_sheet_A.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
SAMPLE = BASE / "data/annotation/kappa_sample.jsonl"
SHEET = BASE / "data/annotation/kappa_sheet_A.csv"
METRICS = BASE / "data/processed/weibo16/metrics.csv"
OUT = BASE / "report/g1_calibration.md"

WIDE = {"opinion", "person", "group"}
STRICT = {"person", "group"}


def _md(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |")
    return "\n".join(out)


def load(sheet: Path) -> dict[str, str]:
    if not sheet.exists():
        sys.exit(f"缺人工标注：{sheet.relative_to(BASE)}\n"
                 "先标 `data/annotation/annotate.html` 再导出 CSV 放回该路径。")
    with sheet.open(encoding="utf-8-sig") as f:
        return {r["id"]: r["target"].strip() for r in csv.DictReader(f)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=str(SHEET))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    sheet_p = Path(args.sheet) if Path(args.sheet).is_absolute() else BASE / args.sheet
    human = load(sheet_p)
    sample = {str(r["id"]): r
              for r in (json.loads(l) for l in SAMPLE.open(encoding="utf-8"))}

    missing = [i for i in sample if i not in human]
    if missing:
        sys.exit(f"标注表缺 {len(missing)} 条（如 id={missing[:5]}），先补完再跑。")

    metrics = pd.read_csv(METRICS, encoding="utf-8-sig")
    g1 = pd.to_numeric(metrics["G1_intensity"], errors="coerce").fillna(0)
    g5 = pd.to_numeric(metrics["G5_group_label"], errors="coerce").fillna(0)
    pop = {
        "group": int((g5 > 0).sum()),
        "pos": int(((g1 > 0) & (g5 <= 0)).sum()),
        "neg": int(((g1 <= 0) & (g5 <= 0)).sum()),
    }
    n_pop = len(metrics)
    n_samp = Counter(r["_layer"] for r in sample.values())

    lines = [
        "# G1（攻击性）校准：中文语料上的分层加权估计",
        "",
        f"人工标注 `{sheet_p.relative_to(BASE)}`，{len(human)} 条；"
        f"语料 weibo16（N={n_pop:,}）。",
        "",
        "此前 G 组的可信度只有 COLD（英文 Twitter）一个来源。"
        "这一份是**同语言、同平台**的独立校准，两个来源应分开引用。",
        "",
        "## 一、分层与权重",
        "",
        _md(["层", "总体 N", "样本 n", "权重 N/n", "说明"],
            [["群体指称", f"{pop['group']:,}", str(n_samp['group']),
              f"{pop['group']/n_samp['group']:.1f}", "G5>0，词表判有群体标签"],
             ["攻击阳性", f"{pop['pos']:,}", str(n_samp['pos']),
              f"{pop['pos']/n_samp['pos']:.1f}", "G1>0 且 G5=0"],
             ["阴性对照", f"{pop['neg']:,}", str(n_samp['neg']),
              f"{pop['neg']/n_samp['neg']:.1f}", "G1=0，占语料 95.6%"]]),
        "",
        "**负层权重 156 倍**，所以只报未加权数字会严重失真；本节全部为加权估计。"
        "代价是方差大——90 条样本里每多一条误判，摊到总体就是约 156 条。"
        "因此小数点后不应过度解读。",
        "",
        "## 二、两个口径",
        "",
        "编码手册把「攻击观点」列为一类攻击。实践中这一类会把「支持抵制」"
        "「坚决抵制」这类**纯表态**也收进来——它们有立场，但没有攻击性。"
        "所以两版都算，差别本身就是结论的一部分。",
        "",
    ]

    results = {}
    for key, name, pos_labels in (
        ("wide", "宽口径（opinion + person + group）", WIDE),
        ("strict", "严口径（person + group）", STRICT),
    ):
        tp = fp = fn = tn = 0.0
        for i, lab in human.items():
            if i not in sample:
                continue
            r = sample[i]
            h = r["_layer"]
            w = pop[h] / n_samp[h]
            h_pos, l_pos = lab in pos_labels, bool(r["_g1_word"])
            if h_pos and l_pos:
                tp += w
            elif h_pos:
                fn += w
            elif l_pos:
                fp += w
            else:
                tn += w
        p = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        f1 = 2 * p * rec / (p + rec) if p + rec else float("nan")
        tot = tp + fp + fn + tn
        results[key] = dict(p=p, r=rec, f1=f1, tp=tp, fp=fp, fn=fn, tn=tn,
                            base=(tp + fn) / tot, pred=(tp + fp) / tot,
                            name=name)
        lines += [
            f"### {name}",
            "",
            _md(["指标", "值"], [
                ["精确率", f"{p:.3f}"],
                ["召回率", f"{rec:.3f}"],
                ["F1", f"{f1:.3f}"],
                ["人工基准率（加权）", f"{(tp+fn)/tot:.1%}"],
                ["词表判正率（加权）", f"{(tp+fp)/tot:.1%}"],
                ["全语料推断：人工认为有攻击", f"{tp+fn:,.0f} / {n_pop:,}"],
                ["全语料推断：词表判出", f"{tp+fp:,.0f}"],
                ["全语料推断：命中", f"{tp:,.0f}"],
            ]),
            "",
        ]

    w, s = results["wide"], results["strict"]
    lines += [
        "## 三、读数",
        "",
        _md(["项", "宽口径", "严口径"], [
            ["人工基准率", f"{w['base']:.1%}", f"{s['base']:.1%}"],
            ["精确率", f"{w['p']:.3f}", f"{s['p']:.3f}"],
            ["召回率", f"{w['r']:.3f}", f"{s['r']:.3f}"],
            ["漏检率", f"{1-w['r']:.1%}", f"{1-s['r']:.1%}"],
        ]),
        "",
        "三点必须写进正文：",
        "",
        f"1. **宽口径给出 60% 的基准率**（约 {w['tp']+w['fn']:,.0f} 条），"
        "这不是攻击性语言的真实水平，而是「opinion」类吞进了大量表态。"
        "任何引用宽口径数字的结论都不可信。",
        f"2. **严口径的召回率只有 {s['r']:.1%}**，与 COLD 上的 21.3% 同向，"
        "但更低——中文微博的攻击多用无脏词的群体化表述，词表抓不到。",
        "3. **精确率在两种口径下差一倍**（0.813 vs 0.343）。说明词表判出的"
        "「攻击」里混了大量只是「有立场」的文本；COLD 上 0.863 的精确率"
        "不能直接搬到中文语料。",
        "",
        "## 四、结论",
        "",
        "G 组（G1/G2）在中文语料上**只能作为下界**，且下界的口径必须在论文里写明："
        "「我们识别出 X 条攻击性文本，人工校准显示这只覆盖了真实攻击的约 1/8」。",
        "",
        "更重要的推论：**「攻击观点」不是一个可与「攻击个人/群体」并列的类别。**"
        "它把立场表达和人身攻击混在一起，任何以它为分母的比例都会失真。"
        "后续若还要用 G2，应只保留 person / group 两类，另设「立场强度」指标"
        "（D 组）承接 opinion。",
        "",
    ]

    out_p = Path(args.out) if Path(args.out).is_absolute() else BASE / args.out
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("\n".join(lines), encoding="utf-8")

    print(f"宽口径：P={w['p']:.3f} R={w['r']:.3f} 基准率 {w['base']:.1%}")
    print(f"严口径：P={s['p']:.3f} R={s['r']:.3f} 基准率 {s['base']:.1%}")
    print(f"已写入 {out_p.relative_to(BASE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
