"""OrChiD：华语辩论赛完整转写。

来源：GitHub `xiutian/OrChiD`（EMNLP 2023），58.8 MB JSON。
结构：

    [{"competition": "2022华语辩论世界杯",
      "match": "1_8决赛_烟台大学VS华南理工大学",
      "topic": "爱是克制_放肆",
      "positions": {"PRO": "爱是克制", "CON": "爱是放肆"},
      "debate": [{"stance": "PRO", "debater": "P1", "utterance": "..."}, ...]}, ...]

在本项目里的角色是**长文本参照集**，不是主分析语料：

- 它是唯一可得的「有句读、有轮次、有立场」的中文讨论语料；
- 但它不是网民发言，是辩论赛，人群与场景都不同，不能拿它推「网友如何说话」；
- 它的用途是回答一个方法论问题：**当文本足够长且有标点时，C 组（论证结构）
  指标能否算出有区分度的值**。若在它上面能算，而微博语料上算不出，
  就证明微博那边的缺口来自语料形态，而不是指标写错了。

字段映射：一场比赛 = 一个 thread；一次发言 = 一个 argument 单元；
发言顺序即 floor_no，因此这份数据也支持「讨论随轮次变化」的分析。
"""

from __future__ import annotations

import json
from pathlib import Path

PLATFORM = "debate"
SOURCE = "orchid"

_STANCE = {"PRO": "pro", "CON": "con"}


def to_units(path: str | Path) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        cand = sorted(p.glob("*.json"))
        if not cand:
            raise FileNotFoundError(f"{p} 下没有 .json")
        p = cand[0]
    if not p.exists():
        raise FileNotFoundError(p)

    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("OrChiD.json 顶层应为 list")

    rows: list[dict] = []
    for i, match in enumerate(data):
        tid = f"orchid-{i:04d}"
        topic = str(match.get("topic") or "")
        for j, seg in enumerate(match.get("debate") or []):
            text = (seg.get("utterance") or "").strip()
            if not text:
                continue
            rows.append({
                "doc_id": f"{tid}-{j:02d}",
                "thread_id": tid,
                "unit": "argument",
                "text": text,
                "platform": PLATFORM,
                "topic_id": topic,
                "topic_title": topic,
                "floor_no": j + 1,
                "author_id": seg.get("debater"),
                "stance": _STANCE.get(str(seg.get("stance", "")).upper(), "neutral"),
                "source": SOURCE,
            })
    return rows
