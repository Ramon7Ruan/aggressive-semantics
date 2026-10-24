"""抽样与信度报告的测试。

抽样错了不会报错，只会让 κ 在某一类上宽到没有意义——所以这里测的是
「三层都抽到了」「没有重复」「配额真的生效」，而不是只测能跑。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """按路径加载 scripts/ 下的脚本（它们不是包的一部分）。"""
    p = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _fake_corpus(n_each: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    """构造一份三类单元都齐的假语料。"""
    rows_u, rows_m = [], []
    for t in ("A", "B"):
        for i in range(n_each):
            doc = f"t-{t}-{i}"
            rows_u.append({"doc_id": doc, "thread_id": f"t-{t}"})
            # 每 10 条：1 条群体候选、3 条阳性、其余阴性
            if i % 10 == 0:
                rows_m.append({"G1_intensity": 0.0, "G2_target": "none",
                               "G5_group_label": 1})
            elif i % 10 < 4:
                rows_m.append({"G1_intensity": 2.0, "G2_target": "person",
                               "G5_group_label": 0})
            else:
                rows_m.append({"G1_intensity": 0.0, "G2_target": "none",
                               "G5_group_label": 0})
    return pd.DataFrame(rows_u), pd.DataFrame(rows_m)


def test_stratified_draws_all_three_layers():
    mk = _load("make_kappa_sample")
    units, metrics = _fake_corpus()
    df, notes = mk._stratified(units, metrics, 3, 4, 2, seed=1)
    assert set(df["layer"]) == {"group", "pos", "neg"}
    # 群体层不能为空：词表几乎测不到群体攻击，这一层是人工 group 的唯一来源
    assert (df["layer"] == "group").sum() >= 2


def test_stratified_has_no_duplicates():
    mk = _load("make_kappa_sample")
    units, metrics = _fake_corpus()
    df, _ = mk._stratified(units, metrics, 3, 4, 2, seed=2)
    assert df["doc_id"].is_unique


def test_stratified_respects_quota_per_topic():
    mk = _load("make_kappa_sample")
    units, metrics = _fake_corpus()
    df, _ = mk._stratified(units, metrics, 3, 4, 2, seed=3)
    for tid, g in df.groupby("thread_id"):
        assert (g["layer"] == "pos").sum() <= 3, tid
        assert (g["layer"] == "neg").sum() <= 4, tid
        assert (g["layer"] == "group").sum() <= 2, tid


def test_stratified_reports_shortfall_instead_of_silently_underfilling():
    mk = _load("make_kappa_sample")
    units, metrics = _fake_corpus(n_each=12)     # 每话题只有 1 条群体候选
    df, notes = mk._stratified(units, metrics, 3, 4, 5, seed=4)
    assert any("群体候选不足" in n for n in notes)


def test_stratified_is_deterministic_with_seed():
    mk = _load("make_kappa_sample")
    units, metrics = _fake_corpus()
    a, _ = mk._stratified(units, metrics, 3, 4, 2, seed=42)
    b, _ = mk._stratified(units, metrics, 3, 4, 2, seed=42)
    assert list(a["doc_id"]) == list(b["doc_id"])


# ── κ 报告 ────────────────────────────────────────────────────────

def test_pair_stats_drops_uncertain_from_denominator():
    kr = _load("kappa_report")
    st = kr._pair_stats(["none", "person", "uncertain", "group"],
                        ["none", "person", "person", "group"])
    assert st["n"] == 3          # uncertain 那条被剔除
    assert st["kappa"] == pytest.approx(1.0)


def test_pair_stats_flags_disagreement():
    kr = _load("kappa_report")
    st = kr._pair_stats(["group", "group", "none", "none"],
                        ["none", "none", "none", "none"])
    assert st["n"] == 4
    assert st["kappa"] < 0.2
    assert st["per_label"]["group"]["recall"] == 0.0


def test_pair_stats_empty_when_no_overlap():
    kr = _load("kappa_report")
    assert kr._pair_stats(["uncertain"], ["uncertain"])["n"] == 0


def test_load_sheet_tolerates_bom_and_extra_columns(tmp_path):
    kr = _load("kappa_report")
    p = tmp_path / "s.csv"
    p.write_text("\ufeffid,target,note\n1,Person,x\n2, group ,y\n", encoding="utf-8")
    out = kr.load_sheet(p)
    assert out == {"1": "person", "2": "group"}
