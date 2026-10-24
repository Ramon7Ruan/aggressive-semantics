"""质量闸门。

定位：**在指标之前拦住「数据本身有问题」的情况。**
指标层的台账回答「这个指标能不能算」，本层回答「这批数据配不配被算」。

分级：
- fatal  —— 直接中止。继续跑只会得到看似合理但无意义的数字。
- warn   —— 记录并继续，但必须在报告里披露。
阈值都写在代码里且给出理由，因为「我觉得差不多」不是验收标准。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .errors import DataQualityError
from .normalize import char_count
from .schema import UNIT_OPENING, UNIT_REPLY, Record

_RE_CJK = re.compile(r"[\u4e00-\u9fff]")

#: 空文本比例上限。超过说明清洗过度或源文件字段映射错了。
MAX_EMPTY_RATIO = 0.05
#: 同帖内完全重复文本的比例上限。超过通常意味着导入了「引用块」而非回复。
MAX_DUP_RATIO = 0.30
#: 中文字符占比下限。低于此值多为编码错误或映射到了错误列。
MIN_CJK_RATIO = 0.30


def _issue(level: str, code: str, message: str, n: int = 0) -> dict:
    return {"level": level, "code": code, "message": message, "n": n}


def run_checks(records: Iterable[Record], *, strict: bool = True) -> list[dict]:
    recs = list(records)
    issues: list[dict] = []
    if not recs:
        raise DataQualityError("没有任何记录；检查导入映射与过滤条件")

    n = len(recs)

    # V1 空文本
    empties = [r for r in recs if char_count(r.text) == 0]
    ratio = len(empties) / n
    if ratio > MAX_EMPTY_RATIO:
        issues.append(_issue("fatal", "V1_empty_text",
                             f"{len(empties)}/{n}（{ratio:.1%}）条文本为空，"
                             f"超过上限 {MAX_EMPTY_RATIO:.0%}", len(empties)))
    elif empties:
        issues.append(_issue("warn", "V1_empty_text",
                             f"{len(empties)} 条文本为空（{ratio:.1%}），已保留但从指标中跳过",
                             len(empties)))

    # V2 doc_id 重复
    dup_ids = [k for k, v in Counter(r.doc_id for r in recs).items() if v > 1]
    if dup_ids:
        issues.append(_issue("fatal", "V2_dup_doc_id",
                             f"{len(dup_ids)} 个 doc_id 重复，例如 {dup_ids[:3]}",
                             len(dup_ids)))

    # V3 同帖完全重复文本
    by_thread: dict[str, list[str]] = {}
    for r in recs:
        by_thread.setdefault(r.thread_id, []).append(char_count(r.text) and r.text.strip())
    dup_total = 0
    for tid, texts in by_thread.items():
        c = Counter(t for t in texts if t)
        dup_total += sum(v - 1 for v in c.values() if v > 1)
    dup_ratio = dup_total / max(n, 1)
    if dup_ratio > MAX_DUP_RATIO:
        issues.append(_issue("warn", "V3_dup_text",
                             f"帖内重复文本占 {dup_ratio:.1%}，超过 {MAX_DUP_RATIO:.0%}；"
                             "常见原因是把引用块当作回复导入", dup_total))

    # V4 无主楼的帖
    threads = {r.thread_id for r in recs}
    with_op = {r.thread_id for r in recs if r.unit == UNIT_OPENING}
    no_op = threads - with_op
    if no_op:
        # 别写成「无法编码」：给了 --topics 就能编码，只是不能靠主楼推断
        issues.append(_issue("warn", "V4_no_opening",
                             f"{len(no_op)}/{len(threads)} 个话题没有主楼（缺 unit=opening），"
                             "无法从主楼正文推断话题类型，须用 --topics 提供人工编码表",
                             len(no_op)))

    # V5 时间倒挂
    bad = 0
    op_time = {r.thread_id: r.created_at for r in recs
               if r.unit == UNIT_OPENING and r.created_at}
    for r in recs:
        t0 = op_time.get(r.thread_id)
        if r.unit == UNIT_REPLY and t0 and r.created_at and r.created_at < t0:
            bad += 1
    if bad:
        issues.append(_issue("warn", "V5_time_order",
                             f"{bad} 条回复早于主楼时间，可能时区或字段映射错误", bad))

    # V6 中文占比
    joined = "".join(r.text for r in recs[:2000])
    cjk = len(_RE_CJK.findall(joined))
    ratio_cjk = cjk / max(len(joined), 1)
    if ratio_cjk < MIN_CJK_RATIO:
        issues.append(_issue("warn", "V6_lang",
                             f"中文字符占比仅 {ratio_cjk:.1%}，"
                             "可能编码错误或映射到了错误列", 0))

    # V7 作者缺失
    no_author = sum(1 for r in recs if not r.author_id)
    if no_author / n > 0.5:
        issues.append(_issue("warn", "V7_author_missing",
                             f"{no_author / n:.0%} 的记录无作者标识，"
                             "参与集中度类指标不可用", no_author))

    # V8 单帖支配
    top = max(Counter(r.thread_id for r in recs).values())
    if top / n > 0.5 and len(threads) > 1:
        issues.append(_issue("warn", "V8_single_thread",
                             f"单个话题占全部单元 {top / n:.0%}，"
                             "帖间比较会退化为个案分析", top))

    if strict:
        fatal = [i for i in issues if i["level"] == "fatal"]
        if fatal:
            msg = "；".join(i["message"] for i in fatal)
            raise DataQualityError(f"质量闸门未通过：{msg}")
    return issues
