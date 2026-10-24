"""COLD：中文冒犯性语言数据集（37,480 条微博评论）。

来源：清华 THU-CoAI，`github.com/thu-coai/COLDataset`，Apache-2.0，
论文 EMNLP 2022（arXiv:2201.06025）。

结构：train/dev/test 三份 csv，列
    (索引) / split / topic(race|region|gender) / label(0 安全,1 冒犯) / TEXT
test.csv 另有 fine-grained-label：
    0 安全(其他非冒犯) / 1 攻击个人 / 2 攻击群体 / 3 安全(反偏见)

它在本项目里的角色不是「主数据」，而是**G 组的金标准**：
我们最担心的失败是「攻击性词典漏掉褒义词反用（睿智/典/孝）导致 G1 系统性偏低」。
COLD 有 3.7 万条人工标注，其中 5323 条还标了攻击对象（个人/群体），
正好用来量化「我们的 G1 到底漏了多少、漏在哪一类」。

这是唯一能让我们诚实地写「G1 是下界，偏差方向已知」的依据。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PLATFORM = "weibo"


def to_units(path: str | Path) -> list[dict]:
    root = Path(path)
    files = [root / f"{s}.csv" for s in ("train", "dev", "test")]
    exist = [f for f in files if f.exists()]
    if not exist:
        raise FileNotFoundError(f"{root} 下没有 train/dev/test.csv")

    rows: list[dict] = []
    for f in exist:
        df = pd.read_csv(f, encoding="utf-8-sig")
        split = f.stem
        # 第一列是 csv 自带的索引，可能与另一 split 重号，doc_id 必须带 split
        for i, rec in df.iterrows():
            text = str(rec.get("TEXT") or "").strip()
            if not text or text == "nan":
                continue
            topic = str(rec.get("topic") or "unknown")
            rows.append({
                "doc_id": f"cold-{split}-{i}",
                "thread_id": f"cold-{topic}",
                "unit": "reply",
                "text": text,
                "platform": PLATFORM,
                "topic_id": topic,
                "topic_title": topic,
                "source": f"cold-{split}",
            })
    return rows
