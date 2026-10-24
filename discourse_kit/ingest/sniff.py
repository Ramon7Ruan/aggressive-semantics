"""结构嗅探：读一个陌生数据文件，报告它到底有什么。

用途很具体：nbsdc 那个 2.9GB 数据集有 34160 个文件、格式混杂
（log;cfg;md;csv;docx）。在写任何导入代码之前，必须先知道
「哪一类文件装的是讨论文本、它有哪些列、能不能对上 schema」。

所以这个模块只输出诊断信息，**不写数据库、不做转换**。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .. import config
from ..errors import IngestError
from ..schema import OPTIONAL, REQUIRED

# ── 编码 ──────────────────────────────────────────────────────────

_CANDIDATE_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16")


def detect_encoding(path: str | Path, probe_bytes: int = 1 << 20) -> str:
    """靠「能否整段解码 + 中文比例」判断编码，不装 chardet。

    只看前 1MB：足够判断，且不会为了一个 2.9GB 的文件读满内存。
    """
    p = Path(path)
    raw = p.open("rb").read(probe_bytes)
    best, best_score = "utf-8", -1.0
    for enc in _CANDIDATE_ENCODINGS:
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if not s:
            continue
        cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
        bad = s.count("\ufffd")
        score = cjk / max(len(s), 1) - bad * 10
        if score > best_score:
            best, best_score = enc, score
    return best


def _read_text(path: str | Path, encoding: str | None, limit_bytes: int) -> str:
    p = Path(path)
    enc = encoding or detect_encoding(p)
    with p.open("rb") as fh:
        raw = fh.read(limit_bytes)
    s = raw.decode(enc, errors="replace")
    # 截断可能在多字节字符中间，去掉末尾残字
    if len(raw) == limit_bytes:
        s = s.rsplit("\n", 1)[0]
    return s


def _detect_delimiter(sample: str) -> str:
    head = sample[:8192]
    try:
        return csv.Sniffer().sniff(head, delimiters=",\t;|").delimiter
    except csv.Error:
        counts = {d: head.count(d) for d in ",\t;|"}
        best = max(counts, key=counts.get)
        return best if counts[best] >= head.count("\n") else ","


# ── 列名同义词 ────────────────────────────────────────────────────
# 命中即建议映射。只做「子串包含」匹配，不做模糊打分——
# 误映射比不映射更危险：不映射会明确报缺字段，误映射会安静地算错指标。
SYNONYMS: dict[str, tuple[str, ...]] = {
    "text": ("正文", "内容", "文本", "评论内容", "回答", "回复内容", "content",
             "text", "body", "message", "comment", "sentence", "utterance"),
    "doc_id": ("doc_id", "id", "编号", "序号", "记录id", "comment_id",
               "answer_id", "floor_id", "unit_id"),
    "thread_id": ("thread_id", "topic_id", "话题id", "帖子id", "文章id",
                  "qa_id", "question_id", "debate_id", "discussion_id", "doc_group"),
    "parent_id": ("parent_id", "reply_to", "父级", "被回复", "in_reply_to"),
    "topic_title": ("标题", "话题", "辩题", "主题", "title", "topic", "question"),
    "author_id": ("用户", "作者", "昵称", "username", "user", "author", "nickname",
                  "user_id", "uid"),
    "posted_at": ("时间", "日期", "发布时间", "创建时间", "time", "date",
                  "created_at", "timestamp", "publish_time"),
    "floor_no": ("楼层", "楼", "floor", "position", "order", "seq"),
    "stance": ("立场", "观点", "stance", "position", "label", "side"),
    "argument_type": ("论据类型", "论点类型", "论证", "argument", "evidence_type"),
    "credibility": ("可信度", "置信", "credibility", "confidence", "reliability"),
    "reply_count": ("回复数", "评论数", "reply_count", "comment_count", "replies"),
    "view_count": ("浏览", "阅读", "view", "read_count", "views"),
    "board": ("版块", "小组", "板块", "board", "group", "channel", "forum"),
    "platform": ("平台", "来源", "platform", "source", "site"),
    "ip_region": ("属地", "ip", "region", "location"),
}


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    """给出建议映射 {canonical_field: 源列名}。

    两轮匹配，顺序很关键：
    1. **整列名精确匹配**优先（「发布时间」→ posted_at）；
    2. 再做子串匹配，且**按关键词长度从长到短**。
       否则「帖子ID」会先被 doc_id 的 `id` 抢走，thread_id 就永远匹配不到——
       这不是理论问题，是实测踩到的。
    一个源列只能映射到一个字段。
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    lowered = {c: str(c).strip().lower() for c in columns}

    for field, keys in SYNONYMS.items():
        for key in keys:
            hit = next((c for c, lc in lowered.items()
                        if lc == key.lower() and c not in used), None)
            if hit is not None:
                out[field] = hit
                used.add(hit)
                break

    pairs = sorted(((k, f) for f, ks in SYNONYMS.items() for k in ks),
                   key=lambda kv: -len(kv[0]))
    for key, field in pairs:
        if field in out:
            continue
        hit = next((c for c, lc in lowered.items()
                    if key.lower() in lc and c not in used), None)
        if hit is not None:
            out[field] = hit
            used.add(hit)
    return out


# ── 主入口 ────────────────────────────────────────────────────────


def sniff_file(path: str | Path, *, rows: int = 200,
               limit_bytes: int = 8 << 20, encoding: str | None = None) -> dict[str, Any]:
    """返回结构诊断报告（可直接 json.dumps）。"""
    p = Path(path)
    if not p.exists():
        raise IngestError(f"文件不存在：{p}")
    suf = p.suffix.lower()
    size = p.stat().st_size

    info: dict[str, Any] = {
        "path": str(p),
        "size_bytes": size,
        "suffix": suf,
    }

    if suf in (".jsonl", ".ndjson"):
        enc = encoding or detect_encoding(p)
        lines = _read_text(p, enc, limit_bytes).splitlines()[:rows]
        objs = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                objs.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        cols = sorted({k for o in objs for k in o})
        info.update({
            "kind": "jsonl",
            "encoding": enc,
            "n_sampled": len(objs),
            "columns": cols,
            "sample": objs[:3],
            "nonnull": {c: round(sum(1 for o in objs if o.get(c) not in (None, ""))
                                 / max(len(objs), 1), 3) for c in cols},
        })

    elif suf in (".json",):
        enc = encoding or detect_encoding(p)
        try:
            obj = json.loads(_read_text(p, enc, limit_bytes))
        except json.JSONDecodeError as exc:
            raise IngestError(f"JSON 解析失败（可能文件大于探测上限）：{exc}") from exc
        first = None
        if isinstance(obj, list) and obj:
            first = obj[0]
        elif isinstance(obj, dict):
            # 找出第一个 list of dict
            for v in obj.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    first = v[0]
                    break
        cols = sorted(first.keys()) if isinstance(first, dict) else []
        info.update({"kind": "json", "encoding": enc, "columns": cols,
                     "sample": first if isinstance(first, dict) else None,
                     "n_sampled": 1})

    elif suf in (".csv", ".tsv", ".txt"):
        enc = encoding or detect_encoding(p)
        sample = _read_text(p, enc, limit_bytes)
        delim = "\t" if suf == ".tsv" else _detect_delimiter(sample)
        reader = csv.reader(io.StringIO(sample), delimiter=delim)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise IngestError("文件为空") from exc
        body = [r for _, r in zip(range(rows), reader) if r]
        nonnull = {}
        for i, col in enumerate(header):
            vals = [r[i] for r in body if i < len(r) and r[i].strip()]
            nonnull[col] = round(len(vals) / max(len(body), 1), 3)
        info.update({
            "kind": "table",
            "encoding": enc,
            "delimiter": delim,
            "columns": header,
            "n_sampled": len(body),
            "nonnull": nonnull,
            "sample": [dict(zip(header, r)) for r in body[:3]],
        })

    else:
        info.update({"kind": "unsupported", "columns": []})

    if info.get("columns"):
        # 全空列不参与建议映射：映射到全空列会得到「导入成功但字段全空」，
        # 这比不映射更危险，因为它不会触发缺字段告警。
        nn = info.get("nonnull") or {}
        cand = [c for c in info["columns"] if nn.get(c, 1.0) > 0]
        sug = suggest_mapping(cand)
        info["suggested_mapping"] = sug
        info["empty_columns"] = [c for c in info["columns"] if nn.get(c, 1.0) == 0]
        info["required_ok"] = [f for f in REQUIRED if f in sug]
        info["required_missing"] = [f for f in REQUIRED if f not in sug]
        info["optional_found"] = [f for f in OPTIONAL if f in sug]
    return info


def format_report(info: dict[str, Any]) -> str:
    """把诊断结果渲染成人类可读的段落（写进报告/CLI 输出）。"""
    lines = [f"文件：{info['path']}"]
    enc = info.get("encoding")
    head = f"大小：{info['size_bytes'] / 1e6:.1f} MB ｜ 类型：{info['kind']}"
    lines.append(head + (f" ｜ 编码：{enc}" if enc else ""))
    if info["kind"] == "table":
        lines.append(f"分隔符：{info['delimiter']!r} ｜ 列数：{len(info['columns'])}")
    lines.append("")
    if info.get("columns"):
        lines.append("列名（采样非空率）：")
        nn = info.get("nonnull", {})
        for c in info["columns"]:
            lines.append(f"  - {c}  ({nn.get(c, float('nan')):.0%})" if c in nn
                         else f"  - {c}")
        lines.append("")
        sug = info.get("suggested_mapping", {})
        if sug:
            lines.append("建议映射：")
            for k, v in sug.items():
                lines.append(f"  {k:14s} ← {v}")
        miss = info.get("required_missing", [])
        lines.append("")
        lines.append("必填字段齐备：" if not miss else f"缺少必填字段：{miss}")
        empty = info.get("empty_columns") or []
        if empty:
            lines.append(f"全空列（不参与建议映射）：{empty}")
    return "\n".join(lines)


def default_mapping_path(name: str) -> Path:
    return Path(config.MAPPING_DIR) / f"{name}.yaml"
