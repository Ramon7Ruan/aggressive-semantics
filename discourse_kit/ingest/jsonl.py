"""标准格式（schema v1）JSONL 直读。

当数据已经整理成规范格式时走这条路：无映射、无猜测、字段名即契约。
这条路径也是所有下游测试的基准输入。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import IngestError, SchemaError
from ..schema import ALL_FIELDS


def import_jsonl(path: str | Path, *, limit: int | None = None,
                 strict: bool = False) -> tuple[list[dict], list[str]]:
    p = Path(path)
    if not p.exists():
        raise IngestError(f"文件不存在：{p}")
    rows: list[dict] = []
    errors: list[str] = []
    with p.open(encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh):
            if limit is not None and i >= limit:
                break
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError as exc:
                msg = f"第 {i + 1} 行 JSON 解析失败：{exc}"
                if strict:
                    raise SchemaError(msg) from exc
                errors.append(msg)
                continue
            unknown = [k for k in obj if k not in ALL_FIELDS and k != "_raw_index"]
            if unknown:
                # 未知字段不是错误，但必须报告——可能是数据集的新字段
                errors.append(f"第 {i + 1} 行含未知字段 {unknown}（已忽略）")
            rows.append({k: v for k, v in obj.items() if k in ALL_FIELDS})
    return rows, errors
