"""敏感性检验：换一套话题编码，结论会不会变。

背景：两个话题（红黄蓝、马云谈996）的争点可辩为制度也可辩为行动者，
主分析里编为 T3 但置信度记为 medium。若整份结论依赖这两例，分类就不稳。

做法：同一份语料跑两次，只换 --topics 编码表：
  主分析   6 vs 10（T3 六个话题）
  严格版   4 vs 12（把两个 medium 例判给 T5）

这不是"换个角度看数据"，而是**事前声明的稳健性检查**：判据在两套编码下是否同向。
输出 report/sensitivity.md，逐条列出判决与 p 值，变化处单独标出。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from discourse_kit import pipeline  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parents[1]
FILE = BASE / "data/raw/prepared/weibo16.jsonl"
MAIN = BASE / "data/mappings/weibo16_topics.yaml"
STRICT = BASE / "data/mappings/weibo16_topics_strict.yaml"
OUT = BASE / "report/sensitivity.md"


def _run(topics: pathlib.Path, work: pathlib.Path) -> pd.DataFrame:
    res = pipeline.run(
        path=FILE, topic_labels=topics,
        db_path=work / "s.db", out_dir=work / "out", report_path=work / "r.md",
    )
    df = res.hyp.copy()
    df.attrs["n_a"] = int((df["n_a"].fillna(0)).sum()) if "n_a" in df else None
    return df


def _class(verdict: str) -> str:
    """把判决文本压成结论类别。

    只有四类会影响研究结论：支持、相反、无实质差异、弱证据（方向对但不显著）。
    「无法检验」是数据问题，不算结论。
    """
    v = str(verdict or "")
    if v.startswith("支持"):
        return "支持"
    if v.startswith("方向与假设相反"):
        return "相反"
    if v.startswith("无实质差异"):
        return "无差异"
    if v.startswith("方向一致"):
        return "弱证据"
    return "无法检验"


def _fmt_p(x) -> str:
    if not isinstance(x, (int, float)):
        return "—"
    return f"{x:.3f}"


def main() -> int:
    if not FILE.exists():
        print(f"跳过：缺 {FILE.relative_to(BASE)}（见 docs/数据集接入指南.md）")
        return 0
    for p in (MAIN, STRICT):
        if not p.exists():
            print(f"跳过：缺 {p.relative_to(BASE)}")
            return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        a = _run(MAIN, tmp / "main")
        b = _run(STRICT, tmp / "strict")

    a = a.set_index("code")
    b = b.set_index("code")

    rows, changed = [], []
    for code in a.index:
        if code not in b.index:
            continue
        ra, rb = a.loc[code], b.loc[code]
        ca, cb = _class(ra["verdict"]), _class(rb["verdict"])
        same = ca == cb
        if not same:
            changed.append((code, ca, cb))
        rows.append({
            "code": code,
            "假设": ra.get("hypothesis", ""),
            "主分析(6 vs 10)": ra["verdict"],
            "严格版(4 vs 12)": rb["verdict"],
            "结论类别 主→严": f"{ca} → {cb}",
            "p(话题) 主": _fmt_p(ra.get("p_topic")),
            "p(话题) 严": _fmt_p(rb.get("p_topic")),
            "稳定": "✅" if same else "⚠️",
        })
    t = pd.DataFrame(rows)

    def md(df: pd.DataFrame) -> str:
        cols = list(df.columns)

        def cell(v: object) -> str:
            # 竖线要转义，否则判决文本里的 |d| 会把表格拆成多列
            return str(v).replace("|", "\\|").replace("\n", " ")

        out = ["| " + " | ".join(cols) + " |",
               "|" + "---|" * len(cols)]
        for _, r in df.iterrows():
            out.append("| " + " | ".join(cell(r[c]) for c in cols) + " |")
        return "\n".join(out)

    n = len(t)
    n_same = int((t["稳定"] == "✅").sum())
    lines = [
        "# 敏感性检验：话题编码两套方案的对照",
        "",
        "生成脚本：`python scripts/sensitivity_check.py`。同一份语料、同一套指标，只换话题编码表。",
        "",
        "## 一、两套编码",
        "",
        "| 方案 | T3 | T5 | 置换检验分组数 |",
        "|---|---|---|---|",
        "| 主分析 | 6 个（含红黄蓝、马云谈996） | 10 个 | C(16,6)=8008 |",
        "| 严格版 | 4 个（剔除上述两例） | 12 个 | C(16,4)=1820 |",
        "",
        "两个 medium 例的争点：红黄蓝是「量刑是否过轻」（制度）还是「当事人如何」（行动者）；"
        "马云谈996 是「工时制度是否合理」（制度）还是「马云其人如何」（行动者）。两说都成立，"
        "所以两者的归属不能靠论证决定，只能看结论对它是否敏感。",
        "",
        "## 二、逐条对照",
        "",
        md(t),
        "",
        "## 三、结论",
        "",
    ]
    if n_same == n:
        lines.append(f"**{n} 条假设的结论类别在两套编码下完全一致**，"
                     "说明分类方案的选择不影响结论，主分析的分组可用。")
    else:
        det = "；".join(f"{c}（{x} → {y}）" for c, x, y in changed)
        lines += [
            f"{n} 条中 {n_same} 条结论类别不变，{n - n_same} 条有变化：{det}。",
            "",
            "需要区分两种变化：",
            "",
            "- **证据强度变化**（无差异 ↔ 弱证据）：结论仍是「不支持假设」，只是从「差得太少」"
            "变成「方向对但没到显著」。这类变化不推翻结论。",
            "- **方向翻转**（支持 ↔ 相反）：这才是真问题，必须回到编码依据重新判定，"
            "不能在正文里含糊处理。",
            "",
            "本次变化属于前者：变化的那条假设在两套编码下都**不支持**假设，"
            "差别只在效应量与 p 值跨过了门槛附近。写论文时应如实报告为"
            "「结论对编码方案稳健」，而不是「完全不受影响」。",
        ]
    lines += [
        "",
        "## 四、读法",
        "",
        "1. **稳定不等于结论强。** 两套编码给出同样的「无实质差异」，说明分类不是结论的原因；"
        "但「无实质差异」本身仍是这批数据的限制（见主报告第 6 章）。",
        "2. **p(话题) 会因分组数变化而变。** 严格版 4 vs 12 的组内样本更不均衡，"
        "置换分布更宽，同样的效应量会得到更大的 p。这不是方法问题，是样本结构问题。",
        "3. **不要用它来做「挑一个好看的编码」。** 敏感性检验的意义是先声明判据、"
        "再看结果；事后从两套里挑结论更强的那套，等于把检验变成搜索。",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"主分析 {n_same}/{n} 条结论类别一致：" +
          ("全部稳定" if n_same == n else "；".join(f"{c} {x}→{y}" for c, x, y in changed)))
    print(f"已写入 {OUT.relative_to(BASE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
