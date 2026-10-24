"""存储层：SQLite 单文件 + DataFrame 查询。

为什么是 SQLite 而不是 Parquet 单文件：
- 需要按 doc_id 幂等 upsert（同一数据集重复导入不能翻倍）
- 需要按 thread 分组取「主楼 + 全部回复」做序列分析
- 单文件、零服务、可以直接放进可复现包
分析结果（指标表）才导出为 CSV/Parquet。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from . import config
from .schema import ALL_FIELDS, Record

DDL = """
CREATE TABLE IF NOT EXISTS units (
    doc_id        TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    unit          TEXT NOT NULL,
    text          TEXT NOT NULL,
    parent_id     TEXT,
    platform      TEXT,
    board         TEXT,
    topic_id      TEXT,
    topic_title   TEXT,
    floor_no      INTEGER,
    author_id     TEXT,
    author_level  INTEGER,
    posted_at     TEXT,
    is_op         INTEGER,
    has_quote     INTEGER,
    quote_text    TEXT,
    is_featured   INTEGER,
    reply_count   INTEGER,
    view_count    INTEGER,
    ip_region     TEXT,
    stance        TEXT,
    argument_type TEXT,
    credibility   TEXT,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_thread ON units(thread_id, floor_no);
CREATE INDEX IF NOT EXISTS idx_units_topic  ON units(topic_id);
CREATE INDEX IF NOT EXISTS idx_units_platform ON units(platform);

CREATE TABLE IF NOT EXISTS labels (
    doc_id     TEXT NOT NULL,
    dimension  TEXT NOT NULL,   -- 如 topic_type / g2_target
    label      TEXT NOT NULL,
    score      REAL,
    method     TEXT,            -- rule / llm / human
    PRIMARY KEY (doc_id, dimension, method)
);
CREATE INDEX IF NOT EXISTS idx_labels_dim ON labels(dimension, label);

CREATE TABLE IF NOT EXISTS ingest_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    source    TEXT,
    file      TEXT,
    n_rows    INTEGER,
    n_ok      INTEGER,
    n_err     INTEGER,
    note      TEXT
);
"""

_BOOL_FIELDS = {"is_op", "has_quote", "is_featured"}


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _to_params(rec: Record) -> tuple:
    out = []
    for f in ALL_FIELDS:
        v = getattr(rec, f)
        if f in _BOOL_FIELDS:
            v = int(bool(v))
        out.append(v)
    return tuple(out)


def upsert_units(conn: sqlite3.Connection, records: Iterable[Record]) -> int:
    """按 doc_id 幂等写入。返回受影响行数。"""
    cols = ", ".join(ALL_FIELDS)
    placeholders = ", ".join("?" for _ in ALL_FIELDS)
    sql = f"INSERT INTO units ({cols}) VALUES ({placeholders}) " \
          f"ON CONFLICT(doc_id) DO UPDATE SET " + \
          ", ".join(f"{f}=excluded.{f}" for f in ALL_FIELDS if f != "doc_id")
    rows = [_to_params(r) for r in records]
    with conn:
        conn.executemany(sql, rows)
    return len(rows)


def log_ingest(conn: sqlite3.Connection, *, source: str, file: str,
               n_rows: int, n_ok: int, n_err: int, note: str = "") -> None:
    from datetime import datetime

    with conn:
        conn.execute(
            "INSERT INTO ingest_log (ts, source, file, n_rows, n_ok, n_err, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), source, file,
             n_rows, n_ok, n_err, note),
        )


def _row_to_record(row: sqlite3.Row) -> Record:
    d = dict(row)
    for f in _BOOL_FIELDS:
        d[f] = bool(d.get(f))
    d.pop("created_at", None)
    return Record(**{f: d.get(f) for f in ALL_FIELDS})


def load_units(conn: sqlite3.Connection, *, where: str = "",
               params: Sequence = ()) -> list[Record]:
    sql = f"SELECT {', '.join(ALL_FIELDS)} FROM units"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY thread_id, COALESCE(floor_no, 0), doc_id"
    return [_row_to_record(r) for r in conn.execute(sql, params)]


def load_df(conn: sqlite3.Connection, sql: str, params: Sequence = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=tuple(params))


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    out["units"] = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    out["threads"] = conn.execute(
        "SELECT COUNT(DISTINCT thread_id) FROM units").fetchone()[0]
    out["openings"] = conn.execute(
        "SELECT COUNT(*) FROM units WHERE unit='opening'").fetchone()[0]
    out["authors"] = conn.execute(
        "SELECT COUNT(DISTINCT author_id) FROM units WHERE author_id IS NOT NULL"
    ).fetchone()[0]
    return out


def put_labels(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    """rows: (doc_id, dimension, label, score, method)。"""
    rows = list(rows)
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "INSERT INTO labels (doc_id, dimension, label, score, method) "
            "VALUES (?,?,?,?,?) ON CONFLICT(doc_id, dimension, method) "
            "DO UPDATE SET label=excluded.label, score=excluded.score",
            rows,
        )
    return len(rows)


def load_labels(conn: sqlite3.Connection, dimension: str,
                method: str | None = None) -> pd.DataFrame:
    sql = "SELECT doc_id, label, score, method FROM labels WHERE dimension=?"
    params: list = [dimension]
    if method:
        sql += " AND method=?"
        params.append(method)
    return load_df(conn, sql, params)
