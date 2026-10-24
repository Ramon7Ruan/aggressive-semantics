"""schema v1：契约本身的测试。

这一层的测试不是在测代码，是在测**数据契约是否真的能容纳异构数据**。
如果这里错了，下游所有指标都会算在错的字段上。
"""

import pytest

from discourse_kit.errors import SchemaError
from discourse_kit.schema import (ALL_FIELDS, OPTIONAL, REQUIRED, capabilities,
                                  coerce, missing_for)


def test_required_fields_enforced():
    with pytest.raises(SchemaError, match="缺少必填字段"):
        coerce({"doc_id": "1", "thread_id": "t", "unit": "reply"})


def test_optional_fields_default_to_none():
    r = coerce({"doc_id": "1", "thread_id": "t", "unit": "reply", "text": "你好"})
    assert r.floor_no is None
    assert r.posted_at is None
    assert r.has_quote is False          # bool 字段默认 False 而不是 None
    assert r.as_dict()["stance"] is None


def test_unit_validated():
    with pytest.raises(SchemaError, match="unit 必须是"):
        coerce({"doc_id": "1", "thread_id": "t", "unit": "comment", "text": "x"})


def test_int_coercion_handles_platform_suffixes():
    r = coerce({"doc_id": "1", "thread_id": "t", "unit": "opening", "text": "x",
                "reply_count": "1.2万", "view_count": "3w", "floor_no": 3.0})
    assert r.reply_count == 12000
    assert r.view_count == 30000
    assert r.floor_no == 3


def test_bool_coercion_accepts_chinese():
    r = coerce({"doc_id": "1", "thread_id": "t", "unit": "reply", "text": "x",
                "has_quote": "是", "is_op": "1"})
    assert r.has_quote is True and r.is_op is True


def test_unknown_keys_are_dropped():
    r = coerce({"doc_id": "1", "thread_id": "t", "unit": "reply", "text": "x",
                "某个数据集特有列": "值"})
    assert "某个数据集特有列" not in r.as_dict()


def test_capabilities_reflect_coverage():
    rows = [
        coerce({"doc_id": "1", "thread_id": "t", "unit": "reply", "text": "a",
                "floor_no": 1}),
        coerce({"doc_id": "2", "thread_id": "t", "unit": "reply", "text": "b"}),
    ]
    caps = capabilities(rows)
    assert caps["floor_no"] == 0.5
    assert caps["reply_count"] == 0.0
    assert set(caps) == set(OPTIONAL)


def test_missing_for_detects_absent_only():
    rows = [coerce({"doc_id": "1", "thread_id": "t", "unit": "reply", "text": "a",
                    "floor_no": 1})]
    assert missing_for(rows, ["floor_no", "author_level"]) == ["author_level"]


def test_every_field_is_declared_once():
    assert len(ALL_FIELDS) == len(set(ALL_FIELDS))
    assert set(REQUIRED).issubset(ALL_FIELDS)
