"""统计与标注一致性：用已知答案校验公式。

这些函数的输出会直接写进论文，算错了没人看得出来——所以必须有可手算的测试点。
"""

import pytest

from discourse_kit.labeling.agreement import (cohen_kappa, krippendorff_alpha_nominal,
                                              macro_f1, precision_recall_f1)
from discourse_kit.labeling.rule_labeler import classify_titled, subtype
from discourse_kit.stats import (bh_fdr, bootstrap_ci, cliffs_delta, cohen_d,
                                 effect_magnitude, permutation_test)


def test_cohen_d_known_value():
    # 两组各 n=3：均值差 2，合并标准差 1 → d = -2（a 更低，故为负）
    a = [1, 2, 3]
    b = [3, 4, 5]
    assert cohen_d(a, b) == pytest.approx(-2.0, abs=1e-6)
    assert effect_magnitude(-1.0) == "大"


def test_cohen_d_needs_two_obs():
    assert cohen_d([1], [2, 3]) is None


def test_cliffs_delta_extremes():
    assert cliffs_delta([3, 4, 5], [1, 2, 3]) == pytest.approx(8 / 9)
    assert cliffs_delta([1, 2], [3, 4]) == pytest.approx(-1.0)
    assert cliffs_delta([1, 2], [1, 2]) == 0.0


def test_permutation_test_detects_clear_difference():
    a = list(range(100, 120))
    b = list(range(0, 20))
    assert permutation_test(a, b, n_perm=500) < 0.01


def test_permutation_test_similar_groups_not_significant():
    a = [1, 2, 3, 4, 5, 6]
    b = [1, 2, 3, 4, 5, 7]
    assert permutation_test(a, b, n_perm=300) > 0.05


def test_bh_fdr_keeps_clear_hits_only():
    pvals = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
    keep = bh_fdr(pvals, alpha=0.05)
    assert keep[0] is True
    assert keep[-1] is False


def test_bootstrap_ci_brackets_mean():
    xs = list(range(50))
    lo, hi = bootstrap_ci(xs, n_boot=500)
    assert lo < sum(xs) / len(xs) < hi


def test_kappa_perfect_and_chance():
    assert cohen_kappa(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)
    # 一方全 a、一方全 b：观测一致率 0，期望一致率 0 → κ 记 0
    assert cohen_kappa(["a", "a"], ["b", "b"]) == 0.0


def test_kappa_known_hand_example():
    # 2x2 表：一致 35/45=0.778，期望一致 0.506 → κ≈0.55
    a = ["x"] * 20 + ["x"] * 5 + ["y"] * 5 + ["y"] * 15
    b = ["x"] * 20 + ["y"] * 5 + ["x"] * 5 + ["y"] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.55, abs=1e-2)


def test_krippendorff_two_coders_equals_kappa_roughly():
    a = ["x", "x", "y", "y", "x"]
    b = ["x", "y", "y", "y", "x"]
    ka = cohen_kappa(a, b)
    al = krippendorff_alpha_nominal([a, b])
    assert abs(ka - al) < 0.35  # 两种系数定义不同，量级应接近


def test_precision_recall_f1():
    y_true = ["a", "a", "b", "b"]
    y_pred = ["a", "b", "b", "b"]
    p, r, f = precision_recall_f1(y_true, y_pred, "a")
    assert p == pytest.approx(1.0)
    assert r == pytest.approx(0.5)
    assert f == pytest.approx(2 / 3)


def test_macro_f1_range():
    assert 0 <= macro_f1(["a", "b"], ["a", "b"], ["a", "b"]) <= 1


# ── 话题编码 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("title,body,expected", [
    ("为什么年轻人不愿意生孩子", "", "T1"),
    ("第一次租房要注意哪些坑", "想问一下押金的事", "T2"),
    ("如何看待城市限牌", "这个政策是否合理", "T3"),
    ("我该不该辞职回家", "", "T4"),
    ("这个主播的学历是不是有问题", "", "T5"),
])
def test_rule_labeler_five_types(title, body, expected):
    assert classify_titled(title, body).code == expected


def test_rule_labeler_unknown_when_no_signal():
    assert classify_titled("随手记一下", "今天天气不错，出去走了走。").code == "T?"


def test_t5_subtype():
    assert subtype("T5", "这人水平不行") == "T5a"
    assert subtype("T1", "为什么") is None
