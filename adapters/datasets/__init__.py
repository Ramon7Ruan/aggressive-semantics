"""公开数据集的转换器：把「别人的格式」摊平成 schema v1。

为什么需要这一层：`data/mappings/*.yaml` 只能处理「一张表、列名可映射」的情况。
而多数公开数据集不是这个形状——微博 stance 数据是 48 个无表头的 txt、
每行空格分词；COLD 是三份 csv 且标签藏在列里。
这种结构必须写代码，但只要输出 schema v1，下游一行都不用改。

约定：每个转换器就是一个函数 `to_units(path) -> list[dict]`，
返回的 dict 用 schema v1 字段名。不读网络、不写数据库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import cold, orchid, weibo16

REGISTRY: dict[str, Callable[[Path], list[dict]]] = {
    "weibo16": weibo16.to_units,
    "cold": cold.to_units,
    "orchid": orchid.to_units,
}

__all__ = ["REGISTRY", "prepare"]


def prepare(name: str, path: str | Path) -> list[dict]:
    """按名字调用转换器。名字不在注册表里就明确报错，不要猜。"""
    if name not in REGISTRY:
        raise KeyError(f"未知数据集 {name!r}，可用：{sorted(REGISTRY)}")
    rows = REGISTRY[name](Path(path))
    if not rows:
        # 与解析器的既有原则一致：0 条是硬失败，不能静默写空库
        raise ValueError(f"{name} 转换出 0 条记录，先检查 {path} 的结构是否变了")

    # 出口校验：id 类字段不得带首尾空白。
    # 真实踩过的坑：weibo16 的「韩国吃播道歉」整个话题 id 尾部带空格，
    # 写进 jsonl 后与 normalize 阶段 strip 过的 id 不一致，同一单元在库里
    # 会存成两条；而按 doc_id 回查原文的脚本会整话题失配、还不报错。
    dirty = [r["doc_id"] for r in rows
             if not isinstance(r.get("doc_id"), str)
             or r["doc_id"] != r["doc_id"].strip()
             or not r["doc_id"]]
    if dirty:
        raise ValueError(
            f"{name} 有 {len(dirty)} 条 doc_id 带首尾空白或为空，"
            f"例：{dirty[0]!r}。请在 adapter 里 strip，不要在出口放行")
    return rows
