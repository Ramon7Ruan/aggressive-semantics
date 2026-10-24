"""指标层：数值正确性 + 失效边界 + 假阳性防护。

这组测试的价值不在于「函数能跑」，而在于把三条研究纪律钉死：
1. 算不出来 → NaN，不是 0；
2. MATTR 不随长度单调下降（TTR 会）；
3. 普通问句不算攻击。
"""

import math

import pandas as pd
import pytest

from discourse_kit.metrics import compute_records
from discourse_kit.metrics.aggression import (form_of, group_labeling, intensity,
                                              target_of)
from discourse_kit.metrics.lexical import mattr
from discourse_kit.metrics.structure import evidence_counts
from discourse_kit.schema import coerce


def rec(doc_id="d1", text="", unit="reply", **kw):
    return coerce({"doc_id": doc_id, "thread_id": kw.pop("thread_id", "t"),
                   "unit": unit, "text": text, **kw})


# ── MATTR ─────────────────────────────────────────────────────────

def test_mattr_perfectly_repetitive_is_low():
    toks = ["a"] * 100
    assert mattr(toks, 50) == pytest.approx(1 / 50)


def test_mattr_all_unique_is_one():
    toks = [str(i) for i in range(100)]
    assert mattr(toks, 50) == pytest.approx(1.0)


def test_mattr_requires_window():
    assert mattr(["a"] * 10, 50) is None


def test_mattr_is_length_stable_while_ttr_is_not():
    """同样的用词习惯，长度翻倍后 MATTR 基本不变，TTR 明显下降。"""
    base = [f"w{i%40}" for i in range(400)]
    longer = base * 4  # 同样的 40 词循环，长度翻四倍
    m1, m2 = mattr(base, 50), mattr(longer, 50)
    t1 = len(set(base)) / len(base)
    t2 = len(set(longer)) / len(longer)
    assert abs(m1 - m2) < 0.05
    assert t2 < t1 - 0.02


# ── 证据 / 论断 ───────────────────────────────────────────────────

def test_evidence_sentence_detection():
    ev, cl, total = evidence_counts("2023年这项支出占比为12%。所以应该降低门槛。")
    assert ev >= 1 and cl >= 1 and total == 2


def test_evidence_counts_empty_text():
    assert evidence_counts("") == (0, 0, 0)


# ── G 组 ──────────────────────────────────────────────────────────

def test_plain_question_is_not_aggression():
    """这条防的是最常见的假阳性：带问号就被判成攻击。"""
    assert intensity("我该不该回去？我很纠结。") == 0.0
    assert intensity("请问这个流程怎么走？") == 0.0


def test_insult_raises_intensity():
    assert intensity("你就是个傻子") > 1.0


def test_irony_is_detected_where_sentiment_fails():
    t = "睿智。查不到就等于造假？逻辑真是绝了。"
    assert intensity(t) > 0
    assert form_of(t) == "irony"


def test_group_labeling_requires_universal_quantifier():
    assert group_labeling("河南人很多") == 0          # 中性提及
    assert group_labeling("河南人都这样") == 1        # 标签化


def test_target_priority_group_over_person():
    assert target_of("这群人都是废物") == "group"
    assert target_of("你就是个废物") == "person"
    assert target_of("这个说法很荒谬") == "none"


# ── 台账与缺失语义 ────────────────────────────────────────────────

def test_metric_unavailable_becomes_nan_not_zero():
    df, ledger = compute_records([rec(text="短")])
    # 短文本 → MATTR 不计算，但列仍存在且为 NaN
    assert "B1_mattr" in df.columns
    assert pd.isna(df.loc["d1", "B1_mattr"])
    row = ledger[ledger["key"] == "B1_mattr"].iloc[0]
    assert row["status"] == "no_valid_value"
    assert row["n_below_min_chars"] == 1


def test_missing_field_marks_metric_skipped():
    df, ledger = compute_records([rec(text="随便一段话")])
    row = ledger[ledger["key"] == "A1_reply_count"].iloc[0]
    assert row["status"] == "skipped_missing_field"
    assert "reply_count" in row["detail"]
    assert "A1_reply_count" not in df.columns


def test_metric_exception_does_not_kill_batch(monkeypatch):
    from discourse_kit.metrics import registry

    def boom(_rec):
        raise RuntimeError("故意失败")

    spec = registry.MetricSpec(key="ZZ_boom", group="Z", name="爆炸",
                               fn=boom, min_chars=0)
    df, ledger = compute_records([rec(text="abc")], specs=[spec])
    row = ledger.iloc[0]
    assert row["status"] == "no_valid_value"
    assert row["n_error"] == 1


def test_all_metrics_have_unique_keys():
    from discourse_kit.metrics.registry import REGISTRY

    keys = list(REGISTRY)
    assert len(keys) == len(set(keys))
    for spec in REGISTRY.values():
        assert spec.requires is not None
        assert isinstance(spec.min_chars, int)


def test_floor_position_metric_requires_floor():
    r = rec(text="一段话", floor_no=5)
    df, _ = compute_records([r])
    assert df.loc["d1", "C9_floor"] == 5
