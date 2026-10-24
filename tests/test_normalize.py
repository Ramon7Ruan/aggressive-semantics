"""规范化：清洗、哈希、时间、分句、分词。

重点测三件容易出错且后果严重的事：
1. 时间解析失败时**不能**偷偷填当前时间（会让「首响时延」变成垃圾）；
2. 作者哈希必须稳定（否则同一人被当成多人）；
3. 分句不能把「对。」独立成句（会拉低平均句长）。
"""

from datetime import datetime

from discourse_kit.normalize import (anonymize, char_count, clean_text, para_count,
                                     parse_time, split_sentences, tokenize,
                                     word_count)


def test_clean_text_strips_html_and_entities():
    raw = "<p>他说&amp;我说</p>\n\n\n\n<b>完</b>"
    out = clean_text(raw)
    assert "<p>" not in out and "&amp;" not in out
    assert "他说&我说" in out
    assert "\n\n\n" not in out


def test_clean_text_keeps_emoji_and_punctuation():
    # emoji 与标点本身是指标，不能被清掉
    assert "🙂" in clean_text("好的🙂！")
    assert "！" in clean_text("好的！")


def test_clean_text_removes_zero_width():
    assert "\u200b" not in clean_text("你\u200b好")


def test_anonymize_is_stable_and_salted():
    a1 = anonymize("张三")
    a2 = anonymize("张三")
    a3 = anonymize("张三", salt="other")
    assert a1 == a2 and a1 != a3
    assert a1.startswith("u_") and len(a1) == 12
    assert anonymize("") is None
    assert anonymize(None) is None


def test_parse_time_full_formats():
    assert parse_time("2024-05-01 12:30:00") == datetime(2024, 5, 1, 12, 30)
    assert parse_time("2024年5月1日") == datetime(2024, 5, 1)
    assert parse_time("2024/5/1 8:00") == datetime(2024, 5, 1, 8, 0)


def test_parse_time_month_day_needs_ref_year():
    assert parse_time("5月1日") is None            # 不猜年份
    assert parse_time("5月1日", ref_year=2023) == datetime(2023, 5, 1)


def test_parse_time_relative_uses_ref():
    ref = datetime(2024, 5, 1, 12, 0)
    assert parse_time("3小时前", ref=ref) == datetime(2024, 5, 1, 9, 0)
    assert parse_time("2天前", ref=ref) == datetime(2024, 4, 29, 12, 0)


def test_parse_time_garbage_returns_none():
    for bad in ("", "刚刚", "昨天下午", "nan", None):
        assert parse_time(bad) is None


def test_split_sentences_merges_short_fragments():
    sents = split_sentences("我不同意。对。因为成本太高了。")
    assert len(sents) == 2
    assert sents[0] == "我不同意。对。"


def test_split_sentences_handles_empty():
    assert split_sentences("") == []


def test_char_count_ignores_whitespace():
    assert char_count("a b\nc") == 3
    assert char_count("你好 世界") == 4


def test_word_count_cjk_plus_latin():
    assert word_count("你好 world") == 3  # 2 个汉字 + 1 个英文词


def test_para_count():
    assert para_count("第一段\n\n第二段") == 2


def test_tokenize_covers_cjk_and_latin():
    toks = tokenize("我说 hello 123")
    assert toks, "分词不应为空"
    assert all(isinstance(t, str) and t for t in toks)


def test_tokenize_strips_urls():
    toks = tokenize("见 https://example.com/abc 的说明")
    assert not any("example" in t for t in toks)
