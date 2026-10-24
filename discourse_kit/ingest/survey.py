"""目录/压缩包普查：一个陌生数据集里，到底哪几个文件装着讨论文本。

`sniff` 解决「这个文件有什么列」，`survey` 解决「这一堆文件里该看哪个」。
两个问题不一样：nbsdc 那个数据集是 2.9GB / 34160 个文件、五种格式混杂，
直接对目录跑 sniff 无从下手，先得知道文件是怎么组织的。

**不解压也能用**：zip 直接读中央目录，只拿「路径 + 大小」，
2.9GB 的包不必落盘就能看清结构。

输出只有诊断信息，不写库、不转换、不改文件。
"""

from __future__ import annotations

import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..errors import IngestError
from .sniff import detect_encoding, sniff_file

# 管线目前能直接读的格式。docx / log / cfg 都要先写转换器。
TABLE_EXTS = {".csv", ".tsv", ".jsonl", ".ndjson", ".json"}
TEXT_EXTS = TABLE_EXTS | {".txt", ".md", ".log", ".cfg", ".ini", ".yaml", ".yml"}
DOC_EXTS = {".docx", ".doc", ".pdf", ".xlsx", ".xls"}

README_HINTS = ("readme", "说明", "描述", "字段", "数据字典", "datadict", "codebook",
                "intro", "guide", "手册")


# ── 枚举三种输入形态 ──────────────────────────────────────────────


def _walk_dir(root: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                out.append((str(p.relative_to(root)), p.stat().st_size))
            except OSError:
                continue
    return out


def _walk_zip(path: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # 中文包名常见 GBK 编码未声明，zipfile 会按 cp437 解成乱码
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("gb18030")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            out.append((name, info.file_size))
    return out


def _walk_tar(path: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    with tarfile.open(path) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            name = m.name
            if not name.isprintable():
                try:
                    name = name.encode("latin-1").decode("gb18030")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            out.append((name, m.size))
    return out


def _enumerate(path: Path) -> tuple[str, list[tuple[str, int]]]:
    if path.is_dir():
        return "dir", _walk_dir(path)
    suf = path.suffix.lower()
    if suf == ".zip":
        return "zip", _walk_zip(path)
    if suf in (".tar", ".gz", ".tgz", ".bz2", ".xz"):
        return "tar", _walk_tar(path)
    raise IngestError(f"不支持的输入：{path}（需要目录 / .zip / .tar.*）")


# ── 分析 ──────────────────────────────────────────────────────────


def _depth(rel: str) -> int:
    return len([x for x in rel.replace("\\", "/").split("/") if x]) - 1


def _group_at(rel: str, level: int) -> str:
    parts = [x for x in rel.replace("\\", "/").split("/") if x]
    if len(parts) <= level:
        return "(上层文件)"
    return "/".join(parts[:level])


def detect_group_level(rels: list[str], max_level: int = 4, min_groups: int = 5) -> int:
    """找出「分组发生」的那一层。

    为什么不能直接取一级目录：压缩包几乎都多套一层总目录
    （`数据集名/topic_01/...`），按一级算只有 1 组，会漏掉主题结构。
    这里从浅到深找**第一个能分出 ≥ min_groups 组**的层级。
    """
    for level in range(1, max_level + 1):
        if len({_group_at(r, level) for r in rels}) >= min_groups:
            return level
    return 1


def _read_preview(root: Path, rel: str, limit: int = 1200,
                  opener: Any = None) -> str:
    """读说明书前若干字。zip 内文件用 opener 传入的 reader。"""
    try:
        if opener is not None:
            raw = opener(rel, limit)
        else:
            with (root / rel).open("rb") as fh:
                raw = fh.read(limit)
    except (OSError, KeyError):
        return ""
    if not raw:
        return ""
    # 复用 sniff 的编码打分：说明书本身也可能是 GBK
    best, best_score = "utf-8", -1.0
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16"):
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
        score = cjk / max(len(s), 1) - s.count("\ufffd") * 10
        if score > best_score:
            best, best_score = enc, score
    return raw.decode(best, errors="replace")


def survey(path: str | Path, *, top: int = 15, probe: int = 0,
           preview: bool = True) -> dict[str, Any]:
    """普查一个目录/压缩包。`probe>0` 时对最大的 N 个表文件跑 sniff。"""
    p = Path(path)
    if not p.exists():
        raise IngestError(f"路径不存在：{p}")

    kind, files = _enumerate(p)
    if not files:
        raise IngestError(f"{p} 里没有文件")

    total = sum(s for _, s in files)
    ext_count = Counter(Path(rel).suffix.lower() or "(无扩展名)" for rel, _ in files)
    ext_size: dict[str, int] = defaultdict(int)
    for rel, s in files:
        ext_size[Path(rel).suffix.lower() or "(无扩展名)"] += s

    largest = sorted(files, key=lambda x: -x[1])[:top]

    # 目录组织形态：判断「一个主题一个目录」还是「平铺」
    rels = [rel for rel, _ in files]
    level = detect_group_level(rels)
    dirs = Counter(_group_at(rel, level) for rel in rels)
    per_dir = [c for d, c in dirs.items() if d != "(上层文件)"]
    depth_hist = Counter(_depth(rel) for rel, _ in files)

    tables = sorted(((rel, s) for rel, s in files
                     if Path(rel).suffix.lower() in TABLE_EXTS), key=lambda x: -x[1])

    readmes = sorted(
        ((rel, s) for rel, s in files
         if Path(rel).suffix.lower() in {".md", ".txt", ".csv"}
         and _depth(rel) <= 1
         and any(h in rel.lower() for h in README_HINTS)),
        key=lambda x: (_depth(x[0]), -x[1]),
    )

    info: dict[str, Any] = {
        "root": str(p),
        "kind": kind,
        "n_files": len(files),
        "size_bytes": total,
        "ext_hist": [
            {"ext": e, "n": ext_count[e], "size_bytes": ext_size[e]}
            for e in sorted(ext_count, key=lambda e: -ext_size[e])
        ],
        "depth_hist": dict(sorted(depth_hist.items())),
        "group_level": level,
        "n_groups": len(per_dir),
        "group_median_files": sorted(per_dir)[len(per_dir) // 2] if per_dir else 0,
        "group_examples": [d for d, _ in dirs.most_common(3)],
        "largest": [{"path": rel, "size_bytes": s} for rel, s in largest],
        "table_candidates": [{"path": rel, "size_bytes": s} for rel, s in tables[:top]],
        "n_table_files": len(tables),
        "readme_candidates": [{"path": rel, "size_bytes": s} for rel, s in readmes[:5]],
    }

    if preview and readmes:
        rel = readmes[0][0]
        if kind == "zip":
            with zipfile.ZipFile(p) as zf:
                raw = zf.read(rel)[:1 << 16]
            text = raw.decode(detect_encoding_bytes(raw), errors="replace")
        else:
            text = _read_preview(p, rel, 1 << 16)
        info["readme_path"] = rel
        info["readme_preview"] = text[:1800]

    if probe and tables:
        probes = []
        for rel, _ in tables[:probe]:
            entry: dict[str, Any] = {"path": rel}
            if kind == "dir":
                try:
                    sub = sniff_file(p / rel, rows=50)
                    entry.update({"columns": sub.get("columns", []),
                                  "suggested_mapping": sub.get("suggested_mapping", {}),
                                  "required_missing": sub.get("required_missing", [])})
                except (IngestError, OSError) as exc:
                    entry["error"] = str(exc)
            else:
                entry["error"] = "压缩包内的表请先解压再 sniff"
            probes.append(entry)
        info["probes"] = probes

    info["warnings"] = _warnings(info)
    return info


def detect_encoding_bytes(raw: bytes) -> str:
    best, best_score = "utf-8", -1.0
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16"):
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
        score = cjk / max(len(s), 1) - s.count("\ufffd") * 10
        if score > best_score:
            best, best_score = enc, score
    return best


def _warnings(info: dict[str, Any]) -> list[str]:
    out: list[str] = []
    n = info["n_files"]
    ext_by_size = {e["ext"]: e for e in info["ext_hist"]}
    n_tab = info["n_table_files"]

    if n_tab == 0:
        out.append("没有任何 csv/tsv/jsonl —— 管线无法直接导入。"
                   "文本可能藏在 log/cfg/docx 里，需要先写一个转换器把这些格式"
                   "摊平成一张表（每行一个发言单元）。")
    elif n_tab <= 3 and n > 100:
        out.append(f"只有 {n_tab} 个表文件，但有 {n} 个文件："
                   "大概率是「少量总表 + 大量单条记录」的混合结构，"
                   "先看总表列名是否齐全；不齐才需要遍历单条记录。")

    docx = ext_by_size.get(".docx")
    if docx and docx["n"] > 0:
        out.append(f"含 {docx['n']} 个 .docx（合计 {docx['size_bytes'] / 1e6:.1f} MB）。"
                   "docx 需要额外依赖才能读，且论文式排版里常混着表格与标题，"
                   "抽取文本时必须标记「这段属于哪一节」，否则会污染指标。")

    if not info["readme_candidates"]:
        out.append("未发现 readme / 说明 / 数据字典。字段含义只能靠列名推断，"
                   "推断结果必须在论文里作为假设写出来，不能当成事实。")

    # 阈值取 5 而非 20：小规模数据集（几百个文件、十几个主题）同样需要
    # 这条提示，而且这句措辞是「很像…若确实如此」，误报代价低。
    if info["n_groups"] >= 5 and info["group_median_files"] >= 2:
        ex = "、".join(info["group_examples"][:2])
        out.append(f"第 {info['group_level']} 层分出 {info['n_groups']} 组，"
                   f"每组中位 {info['group_median_files']} 个文件（如 {ex}）："
                   "很像「一个主题一个目录」的组织方式。"
                   "若确实如此，目录名本身就是天然的分组变量，别丢掉。")

    if info["n_files"] > 5000:
        out.append(f"{info['n_files']} 个文件：不要用 head/cat 逐个看。"
                   "用 survey 的 --probe 抽最大的几个表先列名，"
                   "再决定是全量导入还是写遍历器。")
    return out


# ── 渲染 ──────────────────────────────────────────────────────────


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def format_survey(info: dict[str, Any]) -> str:
    lines = [
        f"路径：{info['root']}",
        f"形态：{info['kind']} ｜ 文件数：{info['n_files']} ｜ 总体积：{_mb(info['size_bytes'])}",
        "",
        "## 格式分布（按体积排序）",
        "",
        "| 扩展名 | 文件数 | 合计体积 | 管线可直读 |",
        "|---|---|---|---|",
    ]
    for e in info["ext_hist"]:
        ok = "是" if e["ext"] in TABLE_EXTS else ("转换后" if e["ext"] in TEXT_EXTS else "否")
        lines.append(f"| {e['ext']} | {e['n']} | {_mb(e['size_bytes'])} | {ok} |")

    lines += ["", "## 目录形态", "",
              f"深度分布：{info['depth_hist']} ｜ 分组层：第 {info['group_level']} 层 "
              f"｜ 分组数：{info['n_groups']}"]

    lines += ["", f"## 最大的 {len(info['largest'])} 个文件", "",
              "| 体积 | 路径 |", "|---|---|"]
    for f in info["largest"]:
        lines.append(f"| {_mb(f['size_bytes'])} | {f['path']} |")

    if info["table_candidates"]:
        lines += ["", "## 可直读的表（优先看这几个）", "",
                  "| 体积 | 路径 |", "|---|---|"]
        for f in info["table_candidates"]:
            lines.append(f"| {_mb(f['size_bytes'])} | {f['path']} |")

    if info["readme_candidates"]:
        lines += ["", "## 说明书候选", ""]
        for f in info["readme_candidates"]:
            lines.append(f"- {f['path']}  ({_mb(f['size_bytes'])})")
        if info.get("readme_preview"):
            lines += ["", f"### 预览：{info.get('readme_path', '')}", "",
                      "```", info["readme_preview"].strip(), "```"]

    for p in info.get("probes", []):
        lines += ["", f"### 抽样列名：{p['path']}", ""]
        if p.get("error"):
            lines.append(f"（{p['error']}）")
            continue
        lines.append(f"列：{p.get('columns', [])}")
        lines.append(f"建议映射：{p.get('suggested_mapping', {})}")
        if p.get("required_missing"):
            lines.append(f"缺必填：{p['required_missing']}")

    if info["warnings"]:
        lines += ["", "## 结论与警告", ""]
        lines += [f"- {w}" for w in info["warnings"]]
    return "\n".join(lines)
