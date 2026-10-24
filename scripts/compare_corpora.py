"""三份语料的横向对照：同一套指标，在不同形态的语料上能算出什么。

为什么要有这个脚本：本研究最容易被质疑的一点是
「你的指标算不出来，是不是代码写错了？」
唯一的回答方式是：**同一份代码，在另一种语料上把同一批指标算出来**。

所以这里固定三份语料，跑同一套指标，比较两件事：
1. 指标可用性（多少指标有值、覆盖率多少）
2. 指标取值（同一指标在不同语料上是否给出有意义的分布）

输出 `report/corpus_comparison.md`，可被报告与论文直接引用。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPORA = [
    ("weibo16", "微博评论（16 个社会议题）", "短、分词后无标点"),
    ("cold", "微博评论（种族/地域/性别）", "中等长度、多数有标点"),
    ("orchid", "华语辩论赛转写", "长文本、完整标点、多轮"),
]

WATCH = ["B0_len", "B3_sent_len", "B5_question_rate", "C4_concession",
         "C5_evidence_rate", "C6_arg_density", "G1_intensity", "G5_group_label"]


def _load(name: str):
    base = ROOT / "data" / "processed" / name
    m = pd.read_csv(base / "metrics.csv", index_col=0)
    cov = pd.read_csv(base / "metric_coverage.csv", encoding="utf-8-sig")
    u = pd.read_csv(base / "units.csv", encoding="utf-8-sig")
    return m, cov, u


def _full_text(name: str, u: pd.DataFrame) -> list[str]:
    """取**未截断**的原文。

    导出的 units.csv 默认把 text 截断（`--text-mode truncate`），
    用它算语料画像会把 OrChiD 的 1000 字发言算成 80 字。
    指标本身是在完整文本上算的，对照表也必须用完整文本，否则比较的不是同一件事。
    """
    import json

    src = ROOT / "data" / "raw" / "prepared" / f"{name}.jsonl"
    if src.exists():
        out = []
        with src.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(str(json.loads(line).get("text") or ""))
        if len(out) == len(u):
            return out
    return [str(t or "") for t in u["text"]]


def _md(df: pd.DataFrame) -> str:
    head = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    body = "\n".join("| " + " | ".join(str(v) for v in row) + " |"
                     for row in df.itertuples(index=False))
    return "\n".join([head, sep, body])


def main() -> int:
    rows = []
    detail = []
    for name, desc, form in CORPORA:
        base = ROOT / "data" / "processed" / name
        if not (base / "metrics.csv").exists():
            print(f"跳过 {name}：尚未跑过管线")
            continue
        m, cov, u = _load(name)
        ok = cov[cov["status"] == "ok"]
        texts = _full_text(name, u)
        lens = pd.Series([len("".join(t.split())) for t in texts])
        from types import SimpleNamespace

        from discourse_kit.quality import profile

        # 直接从原文构造轻量对象：这里只需要 text 与 thread_id。
        # 走 schema.coerce 会把 CSV 里的 NaN 当成非法整数而报错。
        recs = [SimpleNamespace(text=t, thread_id="")
                for t in texts]
        p = profile(recs)
        rows.append({
            "语料": name, "内容": desc, "形态": form,
            "单元": f"{len(u):,}", "话题": u["thread_id"].nunique(),
            "中位字数": f"{lens.median():.0f}",
            "含句末标点": f"{p.sentence_end_rate:.0%}",
            "有效指标": f"{len(ok)}/{len(cov)}",
        })
        d = {"语料": name}
        for key in WATCH:
            if key in m.columns and key in ok["key"].values:
                x = pd.to_numeric(m[key], errors="coerce").dropna()
                d[key] = (f"{x.median():.2f}" if len(x) else "—") + f" (n={len(x):,})"
            elif key in cov["key"].values:
                st = cov.loc[cov["key"] == key, "status"].iloc[0]
                d[key] = {"invalid_for_corpus": "语料不支持",
                          "skipped_missing_field": "缺字段",
                          "no_valid_value": "无有效值"}.get(st, st)
            else:
                d[key] = "—"
        detail.append(d)

    if not rows:
        print("没有任何已处理的语料")
        return 1

    lines = [
        "# 三份语料 × 同一套指标：可用性与取值对照",
        "",
        "生成脚本：`python scripts/compare_corpora.py`。",
        "",
        "## 一、语料形态",
        "",
        _md(pd.DataFrame(rows)),
        "",
        "## 二、关键指标的实际取值",
        "",
        "括号内为参与计算的样本数。`语料不支持` 表示该语料整体不满足指标前提"
        "（如无句末标点导致无法分句），不是「算出来是 0」。",
        "",
        _md(pd.DataFrame(detail)),
        "",
        "## 三、读法",
        "",
        "1. **说话的不是指标，是语料。** 同一份代码在辩论转写上能算出论证密度"
        "与让步结构，在分词后的微博评论上一条都算不出——差别在文本形态，不在实现。",
        "2. **「有值」不等于「有效」。** 早期版本在无标点语料上照样给 B3 返回一个数，"
        "那个数其实是字符数；现在这类指标被整列弃用并记 `invalid_for_corpus`，"
        "宁可留白，不给假数。",
        "3. **短文本不是小号长文本。** 10 字评论能支撑的是词表层判断（是否出现某类词），"
        "不能支撑句法、论证、修辞结构的判断。任何关于「论证方式」的结论，"
        "都必须建立在有句读的语料上。",
        "",
    ]
    # 二元指标在长文本上会饱和：1000 字的发言里「都」几乎必然出现。
    # 这是指标的长度依赖性，必须在对照表里点出来，否则读者会以为
    # 「辩论赛的群体标签化远高于微博」。
    sat = [d for d in detail if d.get("G5_group_label", "").startswith("1.00")]
    if sat:
        lines.append(
            f"4. **`G5_group_label` 在长文本上饱和。** "
            f"{'、'.join(d['语料'] for d in sat)} 的中位数已是 1.00——"
            "不是这些文本全都在做群体标签化，而是 1000 字的发言里「都」"
            "几乎必然出现，二元判定必然触发。该指标是为短文本设计的，"
            "**不可跨语料直接比较**。")
    out = ROOT / "report" / "corpus_comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
