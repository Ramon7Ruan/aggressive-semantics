"""管线编排：一次调用从原始文件走到报告。

刻意做成一个函数而不是一堆散落脚本，因为「跑一遍」这件事本身需要可复现：
输入是什么、经过哪几步、输出了哪几个文件，必须是一处可读的代码。
`repro/run_all.sh` 只是包一层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config, export, hypotheses, quality, store, validate
from .ingest import import_jsonl, import_table
from .labeling import rule_labeler, topics as topic_mod
from .metrics import compute as compute_mod
from .metrics.thread import thread_table
from .normalize import normalize_all
from .report.build import build as build_report
from .schema import ALL_FIELDS, Record, coerce


@dataclass
class PipelineResult:
    n_rows_in: int = 0
    n_records: int = 0
    n_schema_errors: int = 0
    errors: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    ledger: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: pd.DataFrame = field(default_factory=pd.DataFrame)
    hyp: pd.DataFrame = field(default_factory=pd.DataFrame)
    labels: pd.DataFrame = field(default_factory=pd.DataFrame)
    corpus: quality.CorpusProfile = field(default_factory=quality.CorpusProfile)
    topic_table: object | None = None
    paths: dict = field(default_factory=dict)


def load_records(path: str | Path, mapping: str | Path | None = None, *,
                 limit: int | None = None) -> tuple[list[Record], int, list[str]]:
    """读文件 → schema 校验 → Record 列表。返回 (records, 输入行数, 错误)。"""
    p = Path(path)
    if mapping:
        rows, errs = import_table(p, mapping, limit=limit)
    elif p.suffix.lower() in (".jsonl", ".ndjson"):
        rows, errs = import_jsonl(p, limit=limit)
    elif p.suffix.lower() in (".csv", ".tsv") and mapping:
        rows, errs = import_table(p, mapping, limit=limit)
    else:
        raise ValueError(
            f"无法判断如何导入 {p.name}：JSONL 可直读；CSV/表格必须提供 --mapping")

    records: list[Record] = []
    for row in rows:
        clean = {k: v for k, v in row.items() if k in ALL_FIELDS}
        try:
            records.append(coerce(clean))
        except Exception as exc:
            errs.append(str(exc))
    return records, len(rows), errs


def _thread_topic_map(records: list[Record], labels: pd.DataFrame) -> dict[str, str]:
    """doc_id 上的话题标签 → thread_id 上的话题标签。

    话题类型标在主楼上，但研究要按类型比较**回复**。这里做一次下传，
    避免下游到处 merge 时漏掉。
    """
    if labels is None or labels.empty:
        return {}
    tt = labels[labels["dimension"] == "topic_type"]
    doc2code = dict(zip(tt["doc_id"], tt["label"]))
    tid_of_opening = {r.doc_id: r.thread_id for r in records
                      if r.unit == "opening"}
    out: dict[str, str] = {}
    for doc_id, code in doc2code.items():
        tid = tid_of_opening.get(doc_id)
        if tid:
            out[tid] = code
    return out


def run(*, path: str | Path, mapping: str | Path | None = None,
        db_path: str | Path | None = None,
        out_dir: str | Path | None = None,
        report_path: str | Path | None = None,
        topic_labels: str | Path | None = None,
        text_mode: str = "truncate",
        limit: int | None = None,
        source_note: str = "") -> PipelineResult:
    res = PipelineResult()

    # 1 导入 + schema
    records, n_in, errs = load_records(path, mapping, limit=limit)
    res.n_rows_in = n_in
    res.n_schema_errors = len(errs)
    res.errors = errs[:20]
    if not records:
        raise ValueError("导入后没有任何合法记录；先运行 sniff 检查列映射")
    res.n_records = len(records)

    # 2 规范化
    normalize_all(records)

    # 3 质量闸门
    res.issues = validate.run_checks(records, strict=True)

    # 4 语料画像：在算指标之前先判断「这份语料能算什么」
    res.corpus = quality.profile(records)

    # 5 入库
    conn = store.connect(db_path or config.DB_PATH)
    store.init_db(conn)
    store.upsert_units(conn, records)
    store.log_ingest(conn, source=str(records[0].source or ""), file=str(path),
                     n_rows=n_in, n_ok=len(records), n_err=len(errs),
                     note=f"mapping={mapping or 'jsonl'}")
    res.counts = store.counts(conn)

    # 6 话题编码：优先用人工编码表（给定分组），否则退回规则先验
    thread_topic: dict[str, str] = {}
    if topic_labels:
        table = topic_mod.load(topic_labels)
        res.topic_table = table
        missing, extra = topic_mod.check_coverage(
            table, [r.thread_id for r in records])
        if missing:
            raise ValueError(
                "以下话题在语料中出现但编码表未覆盖，会让整组静默消失："
                + ", ".join(missing))
        if extra:
            res.issues.append({
                "code": "W_TOPIC_EXTRA",
                "level": "warn",
                "detail": "编码表里有语料中不存在的话题：" + ", ".join(extra),
            })
        thread_topic = table.codes
        label_rows = table.label_rows() + table.subtype_rows()
    else:
        label_rows = rule_labeler.label_openings(records)
        res.labels = pd.DataFrame(
            label_rows, columns=["doc_id", "dimension", "label", "score", "method"])
        thread_topic = _thread_topic_map(records, res.labels)

    store.put_labels(conn, label_rows)
    res.labels = pd.DataFrame(
        label_rows, columns=["doc_id", "dimension", "label", "score", "method"])

    # 7 指标（传入语料画像，让不成立的指标整列弃用而不是算出假数值）
    metrics_df, ledger = compute_mod.compute_records(records, corpus=res.corpus)
    res.metrics, res.ledger = metrics_df, ledger
    threads = thread_table(records, metrics_df)

    # 8 单元表（合并话题类型，供假设检验）
    # 关键：话题类型是**帖级属性**，必须沿 thread 下传到每条回复。
    # 否则回复子集里 topic_type 全为 NaN，所有分组比较都会静默变成空集。
    units_df = pd.DataFrame([r.as_dict() for r in records], columns=list(ALL_FIELDS))
    units_df["topic_type"] = units_df["thread_id"].map(thread_topic)
    merged = pd.concat([units_df.set_index("doc_id"), metrics_df], axis=1).reset_index()

    # 9 假设
    res.hyp = hypotheses.run_all(merged)

    # 10 导出
    out = Path(out_dir or config.PROCESSED_DIR)
    res.paths = export.write_all(
        out, records=records, metrics=metrics_df, threads=threads,
        ledger=ledger, labels=res.labels, text_mode=text_mode)

    # 11 报告
    rp = Path(report_path or (config.REPORT_DIR / "analysis_report.md"))
    res.paths["report"] = build_report(
        rp,
        info={"source_note": source_note or str(path), "text_mode": text_mode},
        units_df=units_df, metrics=metrics_df, threads=threads,
        ledger=ledger, hyp=res.hyp, labels=res.labels,
        corpus=res.corpus, topic_table=res.topic_table)

    conn.close()
    return res
