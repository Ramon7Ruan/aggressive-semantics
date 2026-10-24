"""adapter 层测试：数据集原文的脏字段与出口校验。

这一层是「平台知识」的唯一入口，所以脏数据必须在这里被清理干净。
漏出去的后果不是报错，而是**下游静默算错**——例如 id 带空格，
normalize 后 strip，同一单元在库里存成两条。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.datasets import REGISTRY, prepare  # noqa: E402
from adapters.datasets import weibo16  # noqa: E402


def _write_weibo(tmp: Path, lines: list[str]) -> Path:
    d = tmp / "train"
    d.mkdir(parents=True, exist_ok=True)
    (d / "外国人.txt").write_text(
        "".join(ln + "\n" for ln in lines), encoding="utf-8")
    return tmp


def test_weibo16_strips_whitespace_in_id_and_stance(tmp_path):
    """真实踩过的坑：某话题整个文件的 id 字段尾部带空格。"""
    root = _write_weibo(tmp_path, [
        "2078 \t外国人\tPOS\t1\t美国 没 资格 干涉",
        "2079\t外国人\tNEG\t3\t我们 也 该 反思",
    ])
    rows = weibo16.to_units(root)
    ids = [r["doc_id"] for r in rows]
    assert ids == ["weibo16-外国人-2078", "weibo16-外国人-2079"]
    assert all(r["doc_id"] == r["doc_id"].strip() for r in rows)
    assert {r["stance"] for r in rows} == {"pro", "con"}


def test_weibo16_topic_name_is_stripped(tmp_path):
    root = _write_weibo(tmp_path, ["1\t外国人 \tPOS\t1\t你好"])
    rows = weibo16.to_units(root)
    assert rows[0]["thread_id"] == "weibo16-外国人"


def test_weibo16_skips_rows_without_id(tmp_path):
    root = _write_weibo(tmp_path, ["\t外国人\tPOS\t1\t有文本但无 id"])
    assert weibo16.to_units(root) == []


def test_prepare_rejects_dirty_doc_id(monkeypatch):
    """出口校验：任何 adapter 放出带空白的 doc_id 都要在 prepare 处失败。

    没有这道校验，脏 id 会一路流到 sqlite 主键与所有 join 里，
    而且全程不报错——这是最难查的一类问题。
    """
    monkeypatch.setitem(REGISTRY, "_dirty_probe",
                        lambda p: [{"doc_id": "x-1 ", "thread_id": "x",
                                    "unit": "reply", "text": "t"}])
    with pytest.raises(ValueError, match="doc_id 带首尾空白"):
        prepare("_dirty_probe", ".")


def test_prepare_rejects_empty_doc_id(monkeypatch):
    monkeypatch.setitem(REGISTRY, "_empty_probe",
                        lambda p: [{"doc_id": "", "thread_id": "x",
                                    "unit": "reply", "text": "t"}])
    with pytest.raises(ValueError, match="doc_id"):
        prepare("_empty_probe", ".")


def test_prepare_rejects_zero_rows(monkeypatch):
    monkeypatch.setitem(REGISTRY, "_zero_probe", lambda p: [])
    with pytest.raises(ValueError, match="0 条记录"):
        prepare("_zero_probe", ".")
