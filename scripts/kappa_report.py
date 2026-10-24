"""双编码信度：算 κ、看混淆矩阵、与词表对照。

三种输入：
  --a   编码者 A 的 CSV（人工，必填）
  --b   编码者 B 的 CSV（第二人，可选；没有就只做「人工 vs 词表」）
  --lexicon  是否附带词表（G2_target）作为机器编码者对照，默认开

为什么一定要看混淆矩阵：κ 会被「多数类占绝对优势」抬高。
两类编码者都把 90% 判成 none 时，κ 可以很好看，但那只是「都不认为有攻击」，
不是「标准一致」。所以逐类 F1 与 uncertain 比例必须同时报。

准入规则写在 config：κ ≥ 0.70 才允许该维度进入假设检验。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from discourse_kit import config  # noqa: E402
from discourse_kit.labeling import agreement as ag  # noqa: E402

SAMPLE = BASE / "data/annotation/kappa_sample.jsonl"
OUT = BASE / "report/kappa.md"
CLASSES = ["none", "opinion", "person", "group"]


def load_sheet(path: Path) -> dict[str, str]:
    """读 id,target 两列。容忍 BOM、空行、多余列。"""
    out: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            i = (row.get("id") or "").strip()
            v = (row.get("target") or "").strip().lower()
            if i and v:
                out[i] = v
    return out


def load_sample(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _fmt(x: float | None, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _rel(p: Path) -> str:
    """路径显示：项目外的文件用绝对路径，别让 relative_to 抛错。"""
    try:
        return str(p.relative_to(BASE))
    except ValueError:
        return str(p)


def _md_table(head: list[str], body: list[list[str]]) -> str:
    def cell(v: object) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(cell(h) for h in head) + " |",
             "|" + "---|" * len(head)]
    for r in body:
        lines.append("| " + " | ".join(cell(c) for c in r) + " |")
    return "\n".join(lines)


def _pair_stats(true: list[str], pred: list[str]) -> dict:
    """只保留双方都是合法类别的配对（uncertain 不进分母）。"""
    keep = [(t, p) for t, p in zip(true, pred) if t in CLASSES and p in CLASSES]
    if not keep:
        return {"n": 0}
    tt = [t for t, _ in keep]
    pp = [p for _, p in keep]
    rep = ag.report(tt, pp, CLASSES)
    rep["agree"] = sum(1 for t, p in keep if t == p) / len(keep)
    rep["pairs"] = keep
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="data/annotation/kappa_sheet_A.csv")
    ap.add_argument("--b", default="data/annotation/kappa_sheet_B.csv")
    ap.add_argument("--sample", default=str(SAMPLE))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-lexicon", action="store_true")
    args = ap.parse_args()

    sample_p = Path(args.sample)
    if not sample_p.is_absolute():
        sample_p = BASE / sample_p
    if not sample_p.exists():
        print(f"缺样本：{_rel(sample_p)}，先跑 scripts/make_kappa_sample.py")
        return 1
    rows = load_sample(sample_p)
    by_id = {str(r["id"]): r for r in rows}

    a_p = Path(args.a) if Path(args.a).is_absolute() else BASE / args.a
    if not a_p.exists():
        print(f"缺编码者 A 的标注：{_rel(a_p)}\n"
              f"先打开 data/annotation/annotate.html 标完并导出 CSV。")
        return 1
    A = load_sheet(a_p)

    b_p = Path(args.b) if Path(args.b).is_absolute() else BASE / args.b
    B = load_sheet(b_p) if b_p.exists() else {}

    ids = [str(r["id"]) for r in rows]
    unc_a = sum(1 for i in ids if A.get(i) == "uncertain")
    unc_b = sum(1 for i in ids if B.get(i) == "uncertain")

    lines = ["# 双编码信度：攻击对象（G2_target 口径）", "",
             f"样本 `{_rel(sample_p)}`，共 {len(rows)} 条，"
             f"覆盖 {len({r['thread_id'] for r in rows})} 个话题。",
             "编码手册：`docs/编码手册_攻击对象.md`。", ""]

    lines += ["## 一、标注概况", ""]
    body = []
    body.append(["编码者 A（人工）", str(sum(1 for i in ids if i in A)),
                 str(unc_a), f"{unc_a / len(rows):.1%}"])
    if B:
        body.append(["编码者 B（人工）", str(sum(1 for i in ids if i in B)),
                     str(unc_b), f"{unc_b / len(rows):.1%}"])
    lines.append(_md_table(
        ["编码者", "已标条数", "其中「拿不准」", "拿不准占比"], body))
    lines.append("")
    lines.append("「拿不准」不进入 κ 的分母。超过 15% 应先补规则重编，"
                 "带着模糊标准算出来的 κ 只是好看。")
    lines.append("")

    # ── 两人一致性 ────────────────────────────────────────────────
    lines += ["## 二、编码者间一致性（A vs B）", ""]
    if not B:
        lines += [
            "**本批标注只有一名编码者，因此没有信度（reliability）可言。**",
            "",
            "这个缺口不能靠加样本或换统计量弥补：两个人独立编同一批材料再比，"
            "是判断「标准是否可重复」的唯一办法。单编码者的判定只能当作"
            "**一份可审计的测量**（判据写在手册里，任何人可以照着重做），"
            "不能当作「已确立的测量」。",
            "",
            "论文里必须如实写「单编码者，未计算编码者间信度」，"
            "不能因为缺少 κ 就干脆不提——审稿人查的是这个位置。",
            "",
            "下面的第三节是**效度**检验（人 vs 机器），与信度是两件事，"
            "不能用它替代 κ，但它本身已经足够判定词表可用与否。",
            "",
        ]
    else:
        common = [i for i in ids
                  if A.get(i) in CLASSES and B.get(i) in CLASSES]
        st = _pair_stats([A[i] for i in common], [B[i] for i in common])
        if st["n"] == 0:
            lines += ["_无重叠的有效标注，无法计算。_", ""]
        else:
            lines += [
                f"参与计算 **{st['n']}** 条（双方都非「拿不准」）。",
                "",
                _md_table(["指标", "值"], [
                    ["Cohen's κ", _fmt(st["kappa"])],
                    ["一致率", f"{st['agree']:.1%}"],
                    ["门槛（config.MIN_KAPPA）", f"{config.MIN_KAPPA:.2f}"],
                ]),
                "",
                "**混淆矩阵**（行 = A，列 = B）：",
                "",
                _md_table(["A \\ B"] + CLASSES,
                          [[c] + [str(st["confusion"].get(f"{c}->{d}", 0))
                                  for d in CLASSES] for c in CLASSES]),
                "",
                "**逐类指标**：",
                "",
                _md_table(["类别", "precision", "recall", "F1", "n(A)"],
                          [[c, _fmt(st["per_label"][c]["precision"]),
                            _fmt(st["per_label"][c]["recall"]),
                            _fmt(st["per_label"][c]["f1"]),
                            str(sum(1 for t, _ in st["pairs"] if t == c))]
                           for c in CLASSES]),
                "",
            ]
            k = st["kappa"]
            if k >= config.MIN_KAPPA:
                lines += [f"**判定：κ = {k:.3f} ≥ {config.MIN_KAPPA:.2f}，"
                          "该维度可用于假设检验。**", ""]
            elif k >= 0.60:
                lines += [f"**判定：κ = {k:.3f}，未达 {config.MIN_KAPPA:.2f}。**"
                          "找出分歧最多的类别补规则，重编那部分，不要直接放行。", ""]
            else:
                lines += [f"**判定：κ = {k:.3f} < 0.60，手册需重写。**"
                          "多半是类别边界没定义清楚，不是编码者不认真。", ""]

    # ── 与词表对照 ────────────────────────────────────────────────
    if not args.no_lexicon:
        lines += ["## 三、人工 vs 词表（G2_target）", ""]
        # 两人都标过时以 A 为准；此处只做「人 vs 机器」的效度对照
        human = {i: A[i] for i in ids if A.get(i) in CLASSES}
        pairs = [(i, human[i], by_id[i].get("_g2_word")) for i in ids
                 if human.get(i) in CLASSES
                 and by_id[i].get("_g2_word") in CLASSES]
        if not pairs:
            lines += ["_无可用对照。_", ""]
        else:
            tt = [p[1] for p in pairs]
            pp = [p[2] for p in pairs]
            st = _pair_stats(tt, pp)
            lines += [
                f"参与计算 **{st['n']}** 条。这是**效度**检验（人 vs 机器），"
                "不是信度：κ 低说明词表漏检/误判，不说明编码者不可靠。",
                "",
                _md_table(["指标", "值"], [
                    ["Cohen's κ", _fmt(st["kappa"])],
                    ["一致率", f"{st['agree']:.1%}"],
                ]),
                "",
                _md_table(["类别", "precision", "recall", "F1", "n(人工)"],
                          [[c, _fmt(st["per_label"][c]["precision"]),
                            _fmt(st["per_label"][c]["recall"]),
                            _fmt(st["per_label"][c]["f1"]),
                            str(sum(1 for t, _ in st["pairs"] if t == c))]
                           for c in CLASSES]),
                "",
            ]
            # 词表最典型的失败模式：人工说 group、词表说 none
            miss = Counter()
            for t, p in zip(tt, pp):
                if t != p:
                    miss[f"{t} → {p}"] += 1
            if miss:
                top = miss.most_common(5)
                lines += ["**最常见的误判方向**：", ""]
                for k2, v in top:
                    lines.append(f"- `{k2}`：{v} 条")
                lines.append("")

            # 边际分布：κ 对边际不平衡极敏感。若人工 21% 判 group 而词表只有 2%，
            # κ 天然上不去——这时该看「漏了什么」，而不是继续调词表。
            def _share(xs, c):
                return sum(1 for x in xs if x == c) / len(xs)

            lines += [
                "**边际分布对比**（同一批样本内的占比）：",
                "",
                _md_table(["类别", "人工", "词表", "差（人工−词表）"],
                          [[c, f"{_share(tt, c):.1%}", f"{_share(pp, c):.1%}",
                            f"{_share(tt, c) - _share(pp, c):+.1%}"]
                           for c in CLASSES]),
                "",
                "边际差这么大时，κ 低是算术必然，不是调参能修好的。"
                "真正该问的是：人工标出的那一类，词表有没有能力识别。",
                "",
            ]

            # 按抽样层拆开：neg 层（词表判无攻击）里人工仍标出攻击 = 漏检；
            # group 层（群体指称候选）里人工判 none = 误报。两者性质不同。
            lay: dict[str, Counter] = {}
            for i in ids:
                lab, r = human.get(i), by_id[i]
                if lab in CLASSES and r.get("_layer"):
                    lay.setdefault(r["_layer"], Counter())[lab] += 1
            if lay:
                order = [x for x in ("group", "pos", "neg") if x in lay]
                zh = {"group": "群体候选", "pos": "攻击阳性", "neg": "阴性对照"}
                lines += [
                    "**按抽样层看人工判定**：",
                    "",
                    _md_table(["抽样层", "n"] + CLASSES,
                              [[zh.get(l, l), str(sum(lay[l].values()))]
                               + [str(lay[l].get(c, 0)) for c in CLASSES]
                               for l in order]),
                    "",
                ]

            # 公平比较：只在词表认为有攻击的条数里比对象，先把「有没有攻击」的分歧排除。
            sub = [(human[i], by_id[i]["_g2_word"]) for i in ids
                   if human.get(i) in CLASSES and by_id[i].get("_g1_word")]
            if sub:
                st2 = _pair_stats([x[0] for x in sub], [x[1] for x in sub])
                lines += [
                    f"**只看词表判为有攻击的 {len(sub)} 条**"
                    "（把「有没有攻击」的分歧排除掉，只比「攻击谁」）：",
                    "",
                    _md_table(["指标", "值"], [
                        ["Cohen's κ", _fmt(st2["kappa"])],
                        ["一致率", f"{st2['agree']:.1%}"],
                    ]),
                    "",
                ]

    # ── 读法 ──────────────────────────────────────────────────────
    lines += [
        "## 四、读法",
        "",
        "1. **κ 与 F1 要一起看。** 样本按三层抽（群体指称 49 / 攻击阳性 107 / 阴性 90），"
        "不是随机抽样，所以**一致率不能当总体准确率读**——要读总体水平请看 "
        "`report/g1_calibration.md` 的分层加权估计。",
        "2. **信度与效度是两件事。** 两人一致（κ 高）只说明标准可重复；"
        "与词表不一致反而可能是词表错——这要靠人工判断，不是靠 κ。"
        "本报告第三节是效度，不能替代第二节的信度。",
        "3. **不确定率是手册质量的信号。** 若「拿不准」集中在某个话题或某类表述，"
        "那是手册缺规则，不是编码者犹豫。",
        "4. **边际分布是判词表的第一现场。** 若人工与词表在某一类的占比差 20pp 以上，"
        "κ 低就是算术必然；这时不要调词表，先问「这一类的定义是不是根本不同」。",
        "",
    ]

    out_p = Path(args.out) if Path(args.out).is_absolute() else BASE / args.out
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("\n".join(lines), encoding="utf-8")

    print(f"编码者 A：{len(A)} 条（拿不准 {unc_a}）；"
          f"编码者 B：{len(B) if B else '未提供'}")
    if B:
        common = [i for i in ids if A.get(i) in CLASSES and B.get(i) in CLASSES]
        k = _pair_stats([A[i] for i in common], [B[i] for i in common]).get("kappa")
        print(f"A vs B：{len(common)} 条可用，κ = {_fmt(k)}")
    print(f"已写入 {_rel(out_p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
