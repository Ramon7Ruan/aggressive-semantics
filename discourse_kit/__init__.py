"""discourse-kit：网络社区话语形态的指标与编码方案。

分层（这条边界是项目能被别人复用的前提）：

    data/raw/*.csv|jsonl
            │  ingest（映射 / 嗅探）
            ▼
        schema v1  ──────────────►  store（SQLite）
            │                          │
            │                          ├─ labeling（T1–T5 / G 组）
            │                          ├─ metrics（B/C/D/G 组）
            │                          └─ hypotheses（H1–H8）
            ▼
        export（CSV）  ──────►  report（Markdown）
"""

__version__ = "0.2.0"

from .schema import (  # noqa: F401
    Record, REQUIRED, OPTIONAL, ALL_FIELDS, UNIT_OPENING, UNIT_REPLY, UNIT_ARGUMENT,
)
