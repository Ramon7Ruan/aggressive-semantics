"""导入层：嗅探 + 映射。

这一层是接入真实数据集的门。测试要覆盖「列名对不上」这种必然发生的情况，
否则拿到 nbsdc 那 34160 个文件时只能靠手工试错。
"""

import pytest
import yaml

from discourse_kit.errors import MappingError
from discourse_kit.ingest import import_jsonl, import_table
from discourse_kit.ingest.sniff import detect_encoding, sniff_file, suggest_mapping


def test_detect_encoding_utf8(messy_csv):
    assert detect_encoding(messy_csv) in ("utf-8-sig", "utf-8")


def test_sniff_table_reports_columns_and_mapping(messy_csv):
    info = sniff_file(messy_csv)
    assert info["kind"] == "table"
    assert "内容" in info["columns"]
    sug = info["suggested_mapping"]
    assert sug.get("text") == "内容"
    assert sug.get("thread_id") == "帖子ID"
    assert sug.get("posted_at") == "发布时间"


def test_sniff_flags_missing_required(messy_csv):
    info = sniff_file(messy_csv)
    # 脏表里没有 unit 列，也没有明确的 doc_id → 必须报告缺失，而不是假装齐备
    assert "unit" in info["required_missing"]


def test_sniff_excludes_empty_columns_from_mapping(messy_csv):
    """全空列不能被建议映射：映射到空列会「导入成功但字段全空」，且不触发缺字段告警。"""
    info = sniff_file(messy_csv)
    assert "回复数" in info["empty_columns"]
    assert "回复数" not in info["suggested_mapping"].values()


def test_suggest_mapping_avoids_double_assignment():
    cols = ["内容", "content", "标题"]
    m = suggest_mapping(cols)
    assert len(set(m.values())) == len(m), "同一列不能被映射到两个字段"


def test_import_table_with_mapping(messy_csv, tmp_path):
    mapping = {
        "source": "test",
        "unit_default": "reply",
        "constants": {"platform": "demo"},
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

    rows, errors = import_table(messy_csv, mp)
    assert rows, "映射后应产出记录"
    assert errors == []
    assert rows[0]["unit"] == "reply"           # unit_default 生效
    assert rows[0]["platform"] == "demo"        # 常量生效
    assert rows[0]["text"]
    assert any(r.get("floor_no") == "0" or r.get("floor_no") == 0 for r in rows)


def test_import_table_rejects_absent_column(messy_csv, tmp_path):
    mapping = {"unit_default": "reply", "fields": {"doc_id": "不存在的列",
                                                   "thread_id": "帖子ID",
                                                   "text": "内容"}}
    mp = tmp_path / "bad.yaml"
    mp.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    with pytest.raises(MappingError, match="不存在"):
        import_table(messy_csv, mp)


def test_mapping_validation_rejects_unknown_field(tmp_path):
    from discourse_kit.ingest.table import load_mapping

    mp = tmp_path / "bad2.yaml"
    mp.write_text(yaml.safe_dump({"fields": {"doc_id": "a", "thread_id": "b",
                                             "text": "c", "unit": "d",
                                             "不存在的字段": "e"}},
                                 allow_unicode=True), encoding="utf-8")
    with pytest.raises(MappingError, match="未知字段"):
        load_mapping(mp)


def test_mapping_validation_requires_unit(tmp_path):
    from discourse_kit.ingest.table import load_mapping

    mp = tmp_path / "bad3.yaml"
    mp.write_text(yaml.safe_dump({"fields": {"doc_id": "a", "thread_id": "b",
                                             "text": "c"}},
                                 allow_unicode=True), encoding="utf-8")
    with pytest.raises(MappingError, match="unit"):
        load_mapping(mp)


def test_import_jsonl_roundtrip(synth_jsonl):
    rows, errors = import_jsonl(synth_jsonl)
    assert len(rows) > 50
    assert errors == []
    assert {"doc_id", "thread_id", "unit", "text"} <= set(rows[0])


def test_import_jsonl_reports_unknown_fields(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"doc_id":"1","thread_id":"t","unit":"reply","text":"a",'
                 '"奇怪字段":1}\n', encoding="utf-8")
    rows, errors = import_jsonl(p)
    assert rows and "未知字段" in errors[0]
