"""表格/JSONL 的字段映射导入。

核心思路：**不写死任何数据集的列名**。

真实数据集的表头是不可预测的（中文、英文、带括号注释、带空格、编号前缀）。
所以导入分成两步：
  1. 用 `sniff` 看结构，得到建议映射；
  2. 把映射固化成 YAML，随仓库一起版本管理。

这样「某数据集怎么映射」是一件可审查、可复现的事，
而不是藏在某个脚本里的隐式假设。

映射格式
--------
```yaml
source: nbsdc-8sBYreNN          # 写进 units.source
file: data/raw/debate.csv
encoding: auto                  # auto / utf-8 / gb18030 ...
delimiter: auto
unit_default: argument          # 源文件没有 unit 列时用它
constants:                      # 每行都补上的常量
  platform: weibo
fields:
  doc_id: 记录编号
  thread_id: [话题ID, 辩题ID]   # 列表 = 取第一个非空
  text: [正文, 内容]
  author_id: 用户名
  posted_at: 发布时间
```
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from ..errors import IngestError, MappingError
from ..schema import OPTIONAL, REQUIRED
from .sniff import _detect_delimiter, _read_text, detect_encoding

_SUPPORTED_SUFFIX = {".csv", ".tsv", ".txt"}


def load_mapping(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise IngestError(f"映射文件不存在：{p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    _validate_mapping(data, p)
    return data


def _validate_mapping(m: dict[str, Any], p: Path) -> None:
    if "fields" not in m or not isinstance(m["fields"], dict):
        raise MappingError(f"{p}: 缺少 fields 段")
    unknown = [k for k in m["fields"] if k not in REQUIRED and k not in OPTIONAL]
    if unknown:
        raise MappingError(
            f"{p}: fields 含未知字段 {unknown}；"
            f"合法字段见 schema.OPTIONAL / REQUIRED")
    missing = [f for f in REQUIRED if f not in m["fields"]
               and f not in ("unit",)]  # unit 可由 unit_default 提供
    if "unit" not in m["fields"] and not m.get("unit_default"):
        missing.append("unit")
    if missing:
        raise MappingError(f"{p}: 缺必填映射 {sorted(set(missing))}")


def _resolve(row: Mapping[str, Any], spec: Any) -> Any:
    """按 spec 取值。spec 可以是 str / list[str] / {columns, join}。"""
    if isinstance(spec, str):
        return row.get(spec)
    if isinstance(spec, (list, tuple)):
        for col in spec:
            v = row.get(col)
            if v not in (None, ""):
                return v
        return None
    if isinstance(spec, dict):
        cols = spec.get("columns") or spec.get("cols") or []
        vals = [_resolve(row, c) for c in cols]
        vals = [str(v) for v in vals if v not in (None, "")]
        joiner = spec.get("join", "_")
        return joiner.join(vals) if vals else None
    raise MappingError(f"无法识别的字段规格：{spec!r}")


def _iter_rows(path: Path, m: dict[str, Any]) -> Iterable[dict[str, Any]]:
    suf = path.suffix.lower()
    enc = m.get("encoding") or detect_encoding(path)
    if enc == "auto":
        enc = detect_encoding(path)

    if suf in (".jsonl", ".ndjson"):
        with path.open(encoding=enc, errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    continue
        return

    if suf == ".json":
        obj = json.loads(path.read_text(encoding=enc, errors="replace"))
        seq = obj
        if isinstance(obj, dict):
            seq = next((v for v in obj.values()
                        if isinstance(v, list) and v and isinstance(v[0], dict)), [])
        for item in seq or []:
            if isinstance(item, dict):
                yield item
        return

    if suf not in _SUPPORTED_SUFFIX:
        raise IngestError(f"不支持的表格类型：{suf}")

    delim = m.get("delimiter")
    if not delim or delim == "auto":
        head = _read_text(path, enc, 1 << 16)
        delim = "\t" if suf == ".tsv" else _detect_delimiter(head)
    with path.open(encoding=enc, errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delim)
        for row in reader:
            yield row


def import_table(path: str | Path, mapping: dict[str, Any] | str | Path, *,
                 limit: int | None = None,
                 strict: bool = False) -> tuple[list[dict], list[str]]:
    """按映射导入，返回 (schema 行字典列表, 错误信息列表)。

    `strict=True` 时遇到第一个坏行即抛错；默认把坏行收集起来继续，
    因为百万行数据集里个别脏行不值得让整次导入失败——但错误必须被看到。
    """
    p = Path(path)
    if not p.exists():
        raise IngestError(f"数据文件不存在：{p}")
    m = load_mapping(mapping) if isinstance(mapping, (str, Path)) else mapping

    constants = dict(m.get("constants") or {})
    fields = m["fields"]
    unit_default = m.get("unit_default")

    # 先确认映射引用的列真的存在（只对表格型做，JSONL 列可能稀疏）
    if p.suffix.lower() in _SUPPORTED_SUFFIX:
        enc = m.get("encoding") or detect_encoding(p)
        if enc == "auto":
            enc = detect_encoding(p)
        head = _read_text(p, enc, 1 << 16)
        delim = m.get("delimiter")
        if not delim or delim == "auto":
            delim = "\t" if p.suffix.lower() == ".tsv" else _detect_delimiter(head)
        header = next(csv.reader([head.split("\n")[0]], delimiter=delim))
        used: list[str] = []
        for spec in fields.values():
            used.extend(_cols_of(spec))
        absent = [c for c in used if c not in header]
        if absent:
            raise MappingError(
                f"映射引用了源文件不存在的列 {absent}；"
                f"源文件前几列是 {header[:12]}")

    rows: list[dict] = []
    errors: list[str] = []
    for i, raw in enumerate(_iter_rows(p, m)):
        if limit is not None and i >= limit:
            break
        rec: dict[str, Any] = dict(constants)
        for field_name, spec in fields.items():
            v = _resolve(raw, spec)
            if v is not None and str(v).strip() != "":
                rec[field_name] = v
        if "unit" not in rec and unit_default:
            rec["unit"] = unit_default
        # 补齐 doc_id / thread_id 的兜底：有些数据集只有自增行号
        for f in ("doc_id", "thread_id"):
            if rec.get(f) in (None, ""):
                rec[f] = f"auto-{f}-{i}"
        rec["_raw_index"] = i
        missing = [f for f in REQUIRED if rec.get(f) in (None, "")]
        if missing:
            msg = f"第 {i + 2} 行缺 {missing}"
            if strict:
                raise MappingError(msg)
            errors.append(msg)
            continue
        rows.append(rec)
    return rows, errors


def _cols_of(spec: Any) -> list[str]:
    if isinstance(spec, str):
        return [spec]
    if isinstance(spec, (list, tuple)):
        out: list[str] = []
        for s in spec:
            out.extend(_cols_of(s))
        return out
    if isinstance(spec, dict):
        return _cols_of(spec.get("columns") or spec.get("cols") or [])
    return []


def write_mapping(path: str | Path, mapping: dict[str, Any]) -> Path:
    """把映射写回 YAML（保持字段顺序，供人审阅）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")
    return p
