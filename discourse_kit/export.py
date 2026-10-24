"""导出层：只出中间格式（CSV / JSON），不出图、不出结论。

这条边界是刻意的。呈现方式一定会变（先表格、后加图、再想做成网页），
如果指标代码里直接 savefig，每次改呈现都要动分析逻辑。
所以：`metrics → 表 → export`，呈现层只消费 export 的产物。

文本导出的三档由 `text_mode` 控制，默认 `truncate`：
- keep     ：保留全文（仅本地分析）
- truncate ：截断到 80 字（报告引用示例用）
- drop     ：不含正文（对外发布的开放数据包）
原始语料属于平台与用户，公开发布时不应随仓分发。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config
from .metrics.registry import codebook
from .schema import ALL_FIELDS, Record

_TEXT_FIELDS = ("text", "quote_text")


def _text_mode(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    out = df.copy()
    if mode == "keep":
        return out
    for col in _TEXT_FIELDS:
        if col not in out.columns:
            continue
        if mode == "drop":
            out = out.drop(columns=[col])
        elif mode == "truncate":
            out[col] = out[col].fillna("").astype(str).str.slice(0, 80)
    return out


def units_table(records: list[Record]) -> pd.DataFrame:
    return pd.DataFrame([r.as_dict() for r in records], columns=list(ALL_FIELDS))


def write_all(out_dir: str | Path, *, records: list[Record],
              metrics: pd.DataFrame, threads: pd.DataFrame,
              ledger: pd.DataFrame, labels: pd.DataFrame | None = None,
              text_mode: str = "truncate") -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    u = _text_mode(units_table(records), text_mode)
    paths["units"] = out / "units.csv"
    u.to_csv(paths["units"], index=False, encoding="utf-8-sig")

    paths["metrics"] = out / "metrics.csv"
    metrics.to_csv(paths["metrics"], encoding="utf-8-sig")

    paths["threads"] = out / "threads.csv"
    threads.to_csv(paths["threads"], index=False, encoding="utf-8-sig")

    paths["coverage"] = out / "metric_coverage.csv"
    ledger.to_csv(paths["coverage"], index=False, encoding="utf-8-sig")

    if labels is not None and not labels.empty:
        paths["labels"] = out / "labels.csv"
        labels.to_csv(paths["labels"], index=False, encoding="utf-8-sig")

    paths["codebook"] = out / "codebook.json"
    paths["codebook"].write_text(
        json.dumps({
            "schema_version": "v1",
            "metrics": codebook(),
            "text_mode": text_mode,
            "n_units": len(records),
            "n_threads": int(threads.shape[0]) if threads is not None else 0,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 一次自检：三张主表必须能被 pandas 原样读回，列数不为 0
    for k in ("units", "metrics", "threads", "coverage"):
        back = pd.read_csv(paths[k], encoding="utf-8-sig")
        if back.shape[1] == 0:
            raise ValueError(f"{paths[k]} 导出后无列，导出层有 bug")
    return paths


def release_pack(out_dir: str | Path, **kwargs) -> dict[str, Path]:
    """对外发布用：不含正文、不含引用原文。"""
    kwargs["text_mode"] = "drop"
    return write_all(out_dir, **kwargs)
