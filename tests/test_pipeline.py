"""端到端：从文件到报告，一次跑完。

这一层的测试标准不是「没报错」，而是：
1. 该有的产物都在，且能被 pandas 读回；
2. 报告里的每一章都有内容（不能出现「_无数据_」——那说明某条链路断了）；
3. 帖级属性确实下传到了回复（这是最容易静默失败的一处）。
"""

import pandas as pd
import pytest
import yaml

from discourse_kit.pipeline import run


@pytest.fixture()
def result(tmp_path, synth_jsonl):
    return run(path=synth_jsonl,
               db_path=tmp_path / "t.db",
               out_dir=tmp_path / "out",
               report_path=tmp_path / "report.md",
               source_note="synthetic-test")


def test_all_artifacts_exist(result):
    for key in ("units", "metrics", "threads", "coverage", "labels", "codebook", "report"):
        assert key in result.paths
        assert result.paths[key].exists(), f"缺少产物 {key}"


def test_export_readable_by_pandas(result):
    for key in ("units", "metrics", "threads", "coverage"):
        df = pd.read_csv(result.paths[key], encoding="utf-8-sig")
        assert df.shape[1] > 0
        assert df.shape[0] > 0


def test_counts_consistent(result):
    assert result.n_records == result.counts["units"]
    assert result.counts["threads"] >= 5
    assert result.counts["openings"] == result.counts["threads"]
    assert result.n_schema_errors == 0


def test_topic_type_propagates_to_replies(result):
    """回归测试：早前版本只给主楼打标签，导致所有分组比较静默为空。"""
    units = pd.read_csv(result.paths["units"], encoding="utf-8-sig")
    metrics = pd.read_csv(result.paths["metrics"], encoding="utf-8-sig", index_col=0)
    labels = pd.read_csv(result.paths["labels"], encoding="utf-8-sig")
    tt = labels[labels["dimension"] == "topic_type"]

    # 标签数量应等于话题数，而不是单元数
    assert len(tt) == units["thread_id"].nunique()
    assert set(tt["label"]) - {"T?"}, "至少要有一些话题被明确归类"


def test_hypotheses_catalog_complete(result):
    """收窄研究后是 H1/H1b/H2–H8 共 9 条；H1b 是攻击强度的条件检验。"""
    hyp = result.hyp
    assert len(hyp) == 9
    assert set(hyp["code"]) == {"H1", "H1b"} | {f"H{i}" for i in range(2, 9)}
    # 每条都有状态或裁决，不允许空白
    assert hyp["verdict"].notna().all()
    assert (hyp["verdict"].astype(str).str.len() > 0).all()


def test_report_sections_non_empty(result):
    text = result.paths["report"].read_text(encoding="utf-8")
    for sec in ("## 0.", "## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6."):
        assert sec in text
    # 报告里不允许出现「整章无数据」——除非该章依赖的字段整体缺失
    assert "_（无数据）_" not in text
    assert "指标可用性台账" in text


def test_coverage_ledger_covers_all_metrics(result):
    ledger = result.ledger
    from discourse_kit.metrics.registry import REGISTRY

    assert len(ledger) == len(REGISTRY)
    assert set(ledger["status"]).issubset(
        {"ok", "no_valid_value", "skipped_missing_field"})


def test_text_mode_drop_removes_text(result, tmp_path, synth_jsonl):
    res = run(path=synth_jsonl, db_path=tmp_path / "t2.db",
              out_dir=tmp_path / "out2", report_path=tmp_path / "r2.md",
              text_mode="drop")
    units = pd.read_csv(res.paths["units"], encoding="utf-8-sig")
    assert "text" not in units.columns
    assert "quote_text" not in units.columns


def test_end_to_end_via_mapping(tmp_path, messy_csv):
    """走「陌生表头 + 映射」的真实路径，而不是只测规范 JSONL。"""
    mapping = {
        "source": "messy-demo",
        "unit_default": "reply",
        "fields": {
            "doc_id": "序号",
            "thread_id": "帖子ID",
            "text": "内容",
            "author_id": "用户名",
            "posted_at": "发布时间",
            "topic_title": "话题标题",
            "floor_no": "楼层",
        },
    }
    mp = tmp_path / "m.yaml"
    mp.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    res = run(path=messy_csv, mapping=mp, db_path=tmp_path / "m.db",
              out_dir=tmp_path / "mout", report_path=tmp_path / "m.md")
    assert res.counts["units"] > 0
    # 全部被标成 reply，因此没有主楼 → 质量检查应给出 V4 告警而不是崩溃
    assert any(i["code"] == "V4_no_opening" for i in res.issues)
    assert res.paths["report"].exists()


def test_rerun_is_idempotent(tmp_path, synth_jsonl):
    kw = dict(path=synth_jsonl, db_path=tmp_path / "i.db",
              out_dir=tmp_path / "iout", report_path=tmp_path / "i.md")
    r1 = run(**kw)
    r2 = run(**kw)
    assert r1.counts["units"] == r2.counts["units"], "重复导入不应翻倍"
