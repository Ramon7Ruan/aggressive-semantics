"""研究设计层的测试：语料画像、话题编码、话题级推断。

这三块是本次收窄研究后新增的，也是最容易被"看起来能跑"掩盖的地方：
- 语料画像判错 → 句法指标在无标点语料上照常出数
- 话题编码漏项 → 某些话题整组从比较中消失，而结果里不留痕迹
- 话题级检验写错 → 退化成「0 与 0 无差异」，p 恒为 1
所以每一块都配一条会失败的用例，而不是只测正常路径。
"""

from __future__ import annotations

import math

import pytest
import yaml

from discourse_kit import quality, stats
from discourse_kit.labeling import topics as topic_mod
from discourse_kit.metrics import compute as compute_mod
from discourse_kit.metrics.registry import REGISTRY
from discourse_kit.schema import coerce


def _recs(texts, *, thread="t1", unit="reply"):
    return [coerce({"doc_id": f"{thread}-{i}", "thread_id": thread,
                    "unit": unit, "text": t}) for i, t in enumerate(texts)]


# ── 语料画像 ──────────────────────────────────────────────────────

def test_profile_detects_unpunctuated_corpus():
    recs = _recs(["外国人 都 这样", "韩国 人 脸 整过", "文科生 没用"])
    p = quality.profile(recs)
    assert p.punctuation_rate == 0.0
    assert p.sentence_end_rate == 0.0
    ok, why = p.check("sentence")
    assert not ok and "句末标点" in why
    assert any("几乎无标点" in w for w in quality.warnings(p))


def test_profile_accepts_punctuated_long_corpus():
    text = ("这是一段有标点的发言。它包含多个句子，因此可以分句。"
            "第三句在这里，长度也够。") * 3
    p = quality.profile(_recs([text, text]))
    assert p.sentence_end_rate == 1.0
    assert p.check("sentence")[0]


def test_profile_flags_short_units():
    p = quality.profile(_recs(["好", "支持", "同意"]))
    assert p.short_share_10 == 1.0
    assert any("单元极短" in w for w in quality.warnings(p))


def test_corpus_gate_invalidates_sentence_metrics():
    """无标点语料上，句法类指标必须整列弃用，而不是返回看似正常的数。"""
    recs = _recs(["外国人 都 这样 垃圾", "韩国人 脸 都 整过 吧"])
    p = quality.profile(recs)
    df, ledger = compute_mod.compute_records(recs, corpus=p)
    row = ledger.set_index("key").loc["B3_sent_len"]
    assert row["status"] == "invalid_for_corpus"
    assert "B3_sent_len" not in df.columns or df["B3_sent_len"].isna().all()


def test_without_corpus_profile_metric_still_computes():
    """传 None 时保持旧行为：用于只关心字段缺失、不关心语料形态的场景。"""
    recs = _recs(["这是一句话。这是第二句话。"])
    df, ledger = compute_mod.compute_records(recs)
    row = ledger.set_index("key").loc["B3_sent_len"]
    assert row["status"] == "ok"
    assert df["B3_sent_len"].notna().sum() == 1


def test_metrics_declaring_needs_exist():
    """防止有人新增句法指标却忘记声明前提。

    这里用显式清单，不做「名字里带 sent 就算」的模糊匹配——
    `D4_sentiment_density` 会因此被误判（sentiment 里也有 sent）。
    """
    needs_sentence = ["B3_sent_len", "B4_sent_len_cv", "B5_question_rate",
                      "C5_evidence_rate", "C6_arg_density", "D7_assertive",
                      "G6_pressure"]
    for k in needs_sentence:
        assert REGISTRY[k].needs == ("sentence",), f"{k} 依赖分句但未声明 needs"
    assert REGISTRY["C1_para"].needs == ("paragraph",)


# ── 话题编码 ──────────────────────────────────────────────────────

def _write_topics(tmp_path, items, prefix="weibo16-"):
    p = tmp_path / "topics.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1, "thread_prefix": prefix, "topics": items,
    }, allow_unicode=True), encoding="utf-8")
    return p


def test_topics_load_and_map_thread_ids(tmp_path):
    p = _write_topics(tmp_path, {
        "疫苗": {"code": "T3", "subtype": "policy"},
        "文科生": {"code": "T5", "subtype": "group"},
    })
    t = topic_mod.load(p)
    assert t.codes == {"weibo16-疫苗": "T3", "weibo16-文科生": "T5"}
    assert t.by_code()["T5"] == ["文科生"]


def test_topics_reject_unknown_code(tmp_path):
    p = _write_topics(tmp_path, {"疫苗": {"code": "T9"}})
    with pytest.raises(ValueError, match="无效取值"):
        topic_mod.load(p)


def test_topics_reject_unknown_subtype(tmp_path):
    p = _write_topics(tmp_path, {"疫苗": {"code": "T3", "subtype": "unknown"}})
    with pytest.raises(ValueError, match="subtype"):
        topic_mod.load(p)


def test_topics_coverage_reports_missing():
    """漏编码的话题必须被报出来：它会让整组静默消失。"""
    t = topic_mod.TopicTable(thread_prefix="x-", topics={
        "a": topic_mod.TopicCoding("a", "x-a", "T3"),
    })
    missing, extra = topic_mod.check_coverage(t, ["x-a", "x-b"])
    assert missing == ["x-b"]
    assert extra == []


def test_label_rows_attach_to_thread_not_doc():
    t = topic_mod.TopicTable(thread_prefix="w-", topics={
        "a": topic_mod.TopicCoding("a", "w-a", "T5", subtype="person"),
    })
    rows = t.label_rows()
    assert rows == [("w-a", "topic_type", "T5", 1.0, "manual")]
    assert t.subtype_rows()[0][1] == "topic_subtype"


# ── 话题级统计 ────────────────────────────────────────────────────

def test_cluster_permutation_is_exact_for_small_designs():
    # 4 vs 4，C(8,4)=70，应走穷举
    values = {f"a{i}": 1.0 for i in range(4)}
    values.update({f"b{i}": 0.0 for i in range(4)})
    groups = {k: ("T3" if k.startswith("a") else "T5") for k in values}
    r = stats.cluster_permutation(values, groups, ("T3",), ("T5",))
    assert r["status"] == "ok"
    assert r["exact"] is True
    # 4 个 1 与 4 个 0：任何「A 组含 3 个及以上 1」或「含 1 个及以下 1」的
    # 分组，两组中位数之差都等于 1（4 个元素的中位数取中间两数均值），
    # 这样的分组共 16+1+16+1 = 34 种，故 p = 34/70。
    assert r["p"] == pytest.approx(34 / 70, abs=1e-4)   # 返回值保留 4 位小数


def test_cluster_permutation_needs_enough_topics():
    values = {"a": 1.0, "b": 2.0, "c": 0.0, "d": 0.1}
    groups = {"a": "T3", "b": "T3", "c": "T5", "d": "T5"}
    r = stats.cluster_permutation(values, groups, ("T3",), ("T5",))
    assert r["status"] == "insufficient_topics"


def test_sign_test_counts_direction():
    r = stats.sign_test([0.1, 0.2, 0.3, -0.1, -0.2, 0.4, 0.5])
    assert (r["pos"], r["neg"]) == (5, 2)
    assert 0 < r["p"] <= 1


def test_icc_is_near_one_when_groups_are_far_apart():
    groups = {"a": [10, 10.1, 9.9, 10.2], "b": [1, 1.1, 0.9, 1.05]}
    r = stats.icc_oneway(groups)
    assert r["icc"] > 0.95


def test_icc_is_low_when_within_variance_dominates():
    groups = {"a": [0, 10, 0, 10], "b": [5, 5, 5, 5.5]}
    r = stats.icc_oneway(groups)
    assert r["icc"] < 0.5


def test_spearman_handles_monotone():
    a = [1, 2, 3, 4, 5]
    b = [2, 4, 6, 8, 10]
    assert stats.spearman(a, b) == pytest.approx(1.0)


def test_permutation_test_is_not_fooled_by_identical_groups():
    a = [0.0] * 200
    b = [0.0] * 200
    assert stats.permutation_test(a, b) == 1.0
    assert math.isclose(stats.cohen_d(a, b), 0.0)


# ── 报告表格完整性 ────────────────────────────────────────────────
# 判决文本里有 `|d|=0.16` 这种写法。markdown 表格里未转义的竖线会把该行拆成
# 多列，整张表错位——而且不会报错，只会让读者把数值对错行。

def test_md_table_escapes_pipe_so_columns_stay_aligned():
    import pandas as pd

    from discourse_kit.report.build import _md_table

    df = pd.DataFrame({
        "code": ["H1b", "H6"],
        "效应量": ["可忽略", "小"],
        "verdict": ["无实质差异（|d|=0.16，相对变化 +3%），不支持假设",
                    "无实质差异（|d|=0.17，相对变化 -17%），不支持假设"],
    })
    out = _md_table(df)
    for line in out.splitlines():
        # 去掉转义对之后，竖线数量必须恒等于「列数 + 1」
        assert line.replace("\\|", "").count("|") == 4, line
    # 内容不能丢：转义后渲染出来仍是 |d|=0.16
    assert "\\|d\\|=0.16" in out


def test_md_table_keeps_newline_out_of_cells():
    import pandas as pd

    from discourse_kit.report.build import _md_table

    df = pd.DataFrame({"a": ["第一行\n第二行"], "b": ["x"]})
    out = _md_table(df)
    assert len(out.splitlines()) == 3          # 表头 + 分隔 + 1 行数据
    assert "第一行 第二行" in out


# ── 报告表格完整性 ────────────────────────────────────────────────
# 判决文本里有 `|d|=0.16` 这种写法。markdown 表格里未转义的竖线会把该行拆成
# 多列，整张表错位——而且不会报错，只会让读者把数值对错行。

def test_md_table_escapes_pipe_so_columns_stay_aligned():
    import pandas as pd

    from discourse_kit.report.build import _md_table

    df = pd.DataFrame({
        "code": ["H1b", "H6"],
        "效应量": ["可忽略", "小"],
        "verdict": ["无实质差异（|d|=0.16，相对变化 +3%），不支持假设",
                    "无实质差异（|d|=0.17，相对变化 -17%），不支持假设"],
    })
    out = _md_table(df)
    for line in out.splitlines():
        # 去掉转义对之后，竖线数量必须恒等于「列数 + 1」
        assert line.replace("\\|", "").count("|") == 4, line
    # 内容不能丢：转义后渲染出来仍是 |d|=0.16
    assert "\\|d\\|=0.16" in out


def test_md_table_keeps_newline_out_of_cells():
    import pandas as pd

    from discourse_kit.report.build import _md_table

    df = pd.DataFrame({"a": ["第一行\n第二行"], "b": ["x"]})
    out = _md_table(df)
    assert len(out.splitlines()) == 3          # 表头 + 分隔 + 1 行数据
    assert "第一行 第二行" in out
