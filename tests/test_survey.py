"""survey 的测试。

重点不是「能跑」，而是三件会误导用户判断的事：
1. 里面有 docx/log 但没有表 → 必须明确说「无法直接导入」，不能静默通过
2. 中文文件名的 zip 不能变成乱码（国内数据集几乎都是 GBK 名的包）
3. 表文件的排序要按体积降序 —— 用户就靠第一条去找主表
"""

from __future__ import annotations

import zipfile

import pytest

from discourse_kit.errors import IngestError
from discourse_kit.ingest.survey import format_survey, survey


def _mk(root, rel, content="x" * 100, encoding="utf-8"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding)
    return p


def test_survey_counts_extension_and_size(tmp_path):
    _mk(tmp_path, "a.csv", "doc_id,text\n1,你好\n")
    _mk(tmp_path, "b.csv", "doc_id,text\n2,再见\n")
    _mk(tmp_path, "logs/x.log", "line\n" * 50)
    info = survey(tmp_path)
    ext = {e["ext"]: e["n"] for e in info["ext_hist"]}
    assert ext[".csv"] == 2
    assert ext[".log"] == 1
    assert info["n_files"] == 3
    assert info["kind"] == "dir"


def test_survey_warns_when_no_table(tmp_path):
    """只有 log/cfg/docx 时必须报警，否则用户会去 sniff 一个 log 然后困惑。"""
    _mk(tmp_path, "a/cfg.txt")
    _mk(tmp_path, "a/dialog.log")
    _mk(tmp_path, "a/report.docx", "fake")
    info = survey(tmp_path)
    assert info["n_table_files"] == 0
    joined = " ".join(info["warnings"])
    assert "无法直接导入" in joined
    assert ".docx" in joined


def test_survey_orders_tables_by_size(tmp_path):
    _mk(tmp_path, "small.csv", "a\n1\n")
    _mk(tmp_path, "big.csv", "a\n" + "1\n" * 20000)
    info = survey(tmp_path)
    names = [t["path"] for t in info["table_candidates"]]
    assert names[0] == "big.csv"


def test_survey_detects_readme_and_previews_it(tmp_path):
    _mk(tmp_path, "README.md", "# 字段说明\n\nlabel: 立场，取值 pro/con\n")
    _mk(tmp_path, "data.csv", "a\n1\n")
    info = survey(tmp_path)
    assert info["readme_path"] == "README.md"
    assert "字段说明" in info["readme_preview"]


def test_survey_reads_zip_without_extracting(tmp_path):
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("data/main.csv", "doc_id,text\n1,你好\n")
        zf.writestr("docs/说明.md", "# 说明\n这是数据集\n")
        zf.writestr("logs/a.log", "x\n")
    info = survey(zpath)
    assert info["kind"] == "zip"
    assert info["n_files"] == 3
    assert [t["path"] for t in info["table_candidates"]] == ["data/main.csv"]
    # 说明书的预览要能读出来（zip 内读，不解压）
    assert "说明" in (info.get("readme_preview") or "")


def test_survey_zip_gbk_filename(tmp_path):
    """国内压缩包常把中文名按 GBK 存且不置 UTF-8 标志位。

    zipfile 会按 cp437 解码，得到乱码文件名；survey 必须转回 GBK，
    否则「说明书.md」会变成一串 ÃÊÂ 之类的东西，用户根本找不到。
    """
    zpath = tmp_path / "gbk.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zi = zipfile.ZipInfo("数据说明.md")
        # 关键：不设置 0x800（UTF-8）标志位，模拟真实的中文包
        zi.flag_bits &= ~0x800
        zf.writestr(zi, "# 字段\n".encode("gb18030"))
    info = survey(zpath)
    names = [f["path"] for f in info["largest"]]
    assert any("数据说明" in n for n in names), names


def test_survey_depth_and_dirs(tmp_path):
    for i in range(6):
        _mk(tmp_path, f"topic{i}/dialog.log")
        _mk(tmp_path, f"topic{i}/meta.cfg")
    info = survey(tmp_path)
    assert info["n_groups"] == 6
    assert info["group_median_files"] == 2
    assert any("一个主题一个目录" in w for w in info["warnings"])


def test_survey_finds_group_level_under_wrapper_dir(tmp_path):
    """压缩包几乎都多套一层总目录。按一级目录算只有 1 组，
    会漏掉「一个主题一个目录」这个关键结构 —— 必须自动下钻。"""
    for i in range(8):
        _mk(tmp_path, f"某某数据集/topic{i}/dialog.log")
        _mk(tmp_path, f"某某数据集/topic{i}/meta.cfg")
    info = survey(tmp_path)
    assert info["group_level"] == 2
    assert info["n_groups"] == 8
    assert any("一个主题一个目录" in w for w in info["warnings"])


def test_survey_unsupported_input(tmp_path):
    p = _mk(tmp_path, "a.bin")
    with pytest.raises(IngestError):
        survey(p)


def test_survey_missing_path(tmp_path):
    with pytest.raises(IngestError):
        survey(tmp_path / "nope")


def test_format_survey_marks_readable(tmp_path):
    _mk(tmp_path, "a.csv", "doc_id,text\n1,你好\n")
    _mk(tmp_path, "a.log", "x\n")
    md = format_survey(survey(tmp_path))
    assert "| .csv | 1 |" in md
    assert "管线可直读" in md
