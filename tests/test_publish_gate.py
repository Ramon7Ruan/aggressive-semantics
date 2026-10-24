"""公开闸门（scripts/check_publish.py）的回归测试。

这个闸门是唯一的防线：`report/*.md` 现在是入仓的，一旦闸门失效，
原始语料和本地路径会静默进公开仓库，而且**不会有任何报错**。
所以它自己要被测。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import check_publish as cp  # noqa: E402


CORPUS = "我觉得写黄书判10年虐童一年半这个判得太轻了，完全不能接受"


def _write(tmp_path: pathlib.Path, name: str, text: str) -> pathlib.Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_short_verbatim_quote_passes(tmp_path):
    p = _write(tmp_path, "a.md", "样例（「写黄书判10年虐童一年半」）说明落点。\n")
    bad, note, st = cp.scan(p, CORPUS)
    assert bad == []
    assert st["verbatim"] == 1
    assert note == []


def test_long_verbatim_quote_fails(tmp_path):
    long_q = "我觉得写黄书判10年虐童一年半这个判得太轻了"
    assert len(long_q) > cp.MAX_QUOTE
    p = _write(tmp_path, "b.md", f"原文（「{long_q}」）\n")
    bad, _, _ = cp.scan(p, CORPUS)
    assert len(bad) == 1
    assert "逐字引用" in bad[0]


def test_absolute_path_fails(tmp_path):
    p = _write(tmp_path, "c.md", "见 /Users/someone/work/data 目录\n")
    bad, _, _ = cp.scan(p, CORPUS)
    assert any("绝对路径" in b for b in bad)


def test_handle_and_long_id_fail(tmp_path):
    p = _write(tmp_path, "d.md", "来自 @某用户 的发言，doc_id 1234567890123\n")
    bad, _, _ = cp.scan(p, CORPUS)
    names = " ".join(bad)
    assert "@handle" in names
    assert "长数字ID" in names


def test_email_and_phone_fail(tmp_path):
    p = _write(tmp_path, "e.md", "联系 a@b.com 或 13800001111\n")
    bad, _, _ = cp.scan(p, CORPUS)
    names = " ".join(bad)
    assert "邮箱" in names
    assert "手机号" in names


def test_own_url_in_github_allowed(tmp_path):
    """仓库自身的链接必须放行，否则报告里不能写自己的地址。"""
    p = _write(tmp_path, "f.md", "见 https://github.com/Ramon7Ruan/aggressive-semantics\n")
    bad, _, _ = cp.scan(p, CORPUS)
    assert bad == []


def test_quote_not_in_corpus_is_not_verbatim(tmp_path):
    """作者自己的话用引号括起来，不能算逐字引用。"""
    p = _write(tmp_path, "g.md", "关键判据是「这一类的定义是不是根本不同」，与语料无关。\n")
    bad, _, st = cp.scan(p, CORPUS)
    assert bad == []
    assert st["verbatim"] == 0


def test_without_corpus_skips_verbatim_check_but_says_so(tmp_path):
    """干净 clone 上没有语料。此时必须明确提示跳过了，不能让「通过」被误读。"""
    long_q = "我觉得写黄书判10年虐童一年半这个判得太轻了"
    p = _write(tmp_path, "h.md", f"原文（「{long_q}」）\n")
    bad, note, st = cp.scan(p, "")
    assert bad == []                      # 无法判定，不误报
    assert st["verbatim"] == 0
    assert any("跳过逐字引用检查" in n for n in note)


def test_placeholder_handle_allowed_but_real_one_not(tmp_path):
    """政策文档里必须能写「不含 @handle」这句话；但真 handle 仍要拦下。"""
    ok = _write(tmp_path, "i.md", "报告不含 @handle、邮箱、手机号。\n")
    bad, _, _ = cp.scan(ok, CORPUS)
    assert bad == []

    real = _write(tmp_path, "j.md", "来自 @张三的日常 的发言\n")
    bad, _, _ = cp.scan(real, CORPUS)
    assert any("@handle" in b for b in bad)


def test_real_reports_pass(tmp_path):
    """仓库里实际存在的报告必须全部通过。这是最贴近交付状态的一条。"""
    root = pathlib.Path(__file__).resolve().parent.parent / "report"
    files = sorted(root.glob("*.md"))
    if not files:
        pytest.skip("report/ 为空")
    for f in files:
        bad, _, _ = cp.scan(f, "") if not cp.load_corpus() else cp.scan(f, cp.load_corpus())
        assert bad == [], f"{f.name}: {bad[:3]}"
