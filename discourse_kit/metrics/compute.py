"""指标计算引擎。

核心行为（也是本层唯一值得记住的事）：
**算不出来的指标不会变成 0，也不会让程序崩，而是被记进「可用性台账」。**

台账是研究报告的必备章节。审稿人问的第一个技术问题通常是
「你怎么处理缺失字段」，答案是这张表，而不是一句「已做缺失值处理」。
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd

from ..normalize import char_count
from ..schema import Record, capabilities
from .registry import MetricSpec, all_specs

# 导入即注册（注册靠装饰器副作用，不导入模块就一个指标都没有）
from . import lexical, structure, aggression, stance, participation  # noqa: F401,E402


def _scope_size(records: Sequence[Record], spec: MetricSpec) -> int:
    return sum(1 for r in records if r.unit in spec.units)


def compute_records(records: Iterable[Record],
                    specs: Sequence[MetricSpec] | None = None,
                    *,
                    raise_on_error: bool = False,
                    corpus: object | None = None,
                    low_coverage: float = 0.05
                    ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """对所有单元计算所有可用指标。

    返回 (指标宽表, 可用性台账)。宽表每行一个 doc_id，每列一个指标 key。

    `corpus` 传入 `quality.CorpusProfile` 时，会先校验每个指标的语料级前提
    （`needs`）。不满足的指标整列不计算，台账记 `invalid_for_corpus`。
    这一行为是**必要的**：否则在无标点语料上，B3 会安静地返回字符数，
    C6 会返回 0 或 1，看起来都是正常数值。
    """
    recs = list(records)
    specs = list(specs or all_specs())
    caps = capabilities(recs)
    available = {f for f, c in caps.items() if c > 0}

    data: dict[str, dict[str, object]] = {}
    ledger: list[dict] = []

    for spec in specs:
        missing = [f for f in spec.requires if f not in available]
        scope = _scope_size(recs, spec)
        if missing:
            ledger.append({
                "key": spec.key, "group": spec.group, "name": spec.name,
                "status": "skipped_missing_field",
                "detail": "缺字段：" + ", ".join(missing),
                "n_scope": scope, "n_ok": 0, "n_below_min_chars": 0,
                "n_none": 0, "n_error": 0, "coverage": 0.0, "warning": "",
            })
            continue

        if spec.needs and corpus is not None:
            reasons = []
            for need in spec.needs:
                ok, why = corpus.check(need)  # type: ignore[attr-defined]
                if not ok:
                    reasons.append(why)
            if reasons:
                ledger.append({
                    "key": spec.key, "group": spec.group, "name": spec.name,
                    "status": "invalid_for_corpus",
                    "detail": "；".join(reasons),
                    "n_scope": scope, "n_ok": 0, "n_below_min_chars": 0,
                    "n_none": 0, "n_error": 0, "coverage": 0.0,
                    "warning": "语料形态不支持，数值会失真，已整列弃用",
                })
                continue

        values: dict[str, object] = {}
        n_ok = n_short = n_none = n_err = 0
        errors: list[str] = []
        for r in recs:
            if r.unit not in spec.units:
                continue
            if char_count(r.text) < spec.min_chars:
                n_short += 1
                continue
            try:
                v = spec.fn(r)
            except Exception as exc:  # 单个指标的 bug 不该毁掉整批
                n_err += 1
                if len(errors) < 3:
                    errors.append(f"{r.doc_id}: {exc!r}")
                if raise_on_error:
                    raise
                continue
            if v is None:
                n_none += 1
            else:
                values[r.doc_id] = v
                n_ok += 1

        data[spec.key] = values
        status = "ok" if n_ok else "no_valid_value"
        detail = ""
        if n_err:
            detail = f"{n_err} 次异常；示例 {errors[:2]}"
        elif not n_ok:
            detail = "全部样本不满足前提（文本过短或无有效取值）"
        coverage = round(n_ok / scope, 3) if scope else 0.0
        warning = ""
        if status == "ok" and coverage < low_coverage:
            warning = f"覆盖率仅 {coverage:.1%}，样本量太少，不宜据此下结论"
        ledger.append({
            "key": spec.key, "group": spec.group, "name": spec.name,
            "status": status, "detail": detail,
            "n_scope": scope, "n_ok": n_ok, "n_below_min_chars": n_short,
            "n_none": n_none, "n_error": n_err,
            "coverage": coverage, "warning": warning,
        })

    if not data:
        return pd.DataFrame(index=pd.Index([r.doc_id for r in recs])), pd.DataFrame(ledger)

    df = pd.DataFrame({k: pd.Series(v, dtype="object") for k, v in data.items()},
                      index=pd.Index([r.doc_id for r in recs], name="doc_id"))
    # 数值列转 float，categorical 保持 object
    kinds = {s.key: s.kind for s in specs}
    for col in df.columns:
        if kinds.get(col) == "numeric":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df, pd.DataFrame(ledger)
