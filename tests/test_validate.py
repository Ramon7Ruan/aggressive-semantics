"""质量闸门：fatal 必须拦住，warn 必须放行并记账。

分界的判断标准是「继续跑会不会得出看似合理的错误结论」：
空文本率过高 → 会（指标全算在空字符串上），必须拦；
缺少主楼 → 不会（只是编不出话题类型），放行但要披露。
"""

import pytest

from discourse_kit.errors import DataQualityError
from discourse_kit.schema import coerce
from discourse_kit.validate import run_checks


def r(i, text="一段正常的文字内容", unit="reply", thread="t1", **kw):
    return coerce({"doc_id": str(i), "thread_id": thread, "unit": unit,
                   "text": text, **kw})


def test_empty_input_raises():
    with pytest.raises(DataQualityError, match="没有任何记录"):
        run_checks([])


def test_high_empty_ratio_is_fatal():
    recs = [r(i, text="   ") for i in range(10)] + [r(99)]
    with pytest.raises(DataQualityError, match="文本为空"):
        run_checks(recs)


def test_low_empty_ratio_is_warning():
    recs = [r(i) for i in range(19)] + [r(99, text="   ")]
    issues = run_checks(recs)
    assert any(i["code"] == "V1_empty_text" and i["level"] == "warn" for i in issues)


def test_duplicate_doc_id_is_fatal():
    a = r(1)
    b = r(1)
    with pytest.raises(DataQualityError, match="doc_id 重复"):
        run_checks([a, b])


def test_missing_opening_is_warning_only():
    issues = run_checks([r(1), r(2)])
    v4 = [i for i in issues if i["code"] == "V4_no_opening"]
    assert v4 and v4[0]["level"] == "warn"


def test_time_order_violation_detected():
    recs = [
        r(1, unit="opening", posted_at="2024-05-01 12:00:00"),
        r(2, posted_at="2024-05-01 10:00:00"),
    ]
    for x in recs:
        from discourse_kit.normalize import normalize_record
        normalize_record(x)
    issues = run_checks(recs)
    assert any(i["code"] == "V5_time_order" for i in issues)


def test_non_chinese_corpus_warns():
    issues = run_checks([r(i, text="this is english text only") for i in range(5)])
    assert any(i["code"] == "V6_lang" for i in issues)


def test_clean_corpus_has_no_fatal():
    recs = [r(1, unit="opening")] + [r(i) for i in range(2, 6)]
    assert all(i["level"] != "fatal" for i in run_checks(recs))
