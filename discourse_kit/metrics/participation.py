"""A 组参与强度 / E 组社群位置。

这两组在本项目里是**控制变量**，不是研究焦点。
理由：数据集往往缺少浏览量、用户等级、关注关系等字段，
把它们当主变量会让整篇研究受制于数据集的偶然完整度。
它们的作用是：当某一组差异出现时，用来排除「只是活跃用户更多」这类替代解释。
"""

from __future__ import annotations

from ..schema import Record
from .registry import metric


@metric("A1_reply_count", group="A", name="帖内回复总数", requires=("reply_count",),
        note="话题热度。跨平台不可比（统计口径不同），只在平台内使用")
def a1(rec: Record) -> int | None:
    return rec.reply_count


@metric("A2_view_count", group="A", name="浏览量", requires=("view_count",))
def a2(rec: Record) -> int | None:
    return rec.view_count


@metric("A3_is_op", group="A", name="是否发起者", kind="categorical",
        note="主楼=1。用于区分「框定议题的人」与「回应议题的人」")
def a3(rec: Record) -> int:
    return int(bool(rec.is_op))


@metric("E1_author_level", group="E", name="账号等级", requires=("author_level",),
        note="平台资历的代理变量。缺失率高时不要用")
def e1(rec: Record) -> int | None:
    return rec.author_level


@metric("E2_featured", group="E", name="平台精选", requires=("is_featured",),
        kind="categorical", note="平台级认可标记，比用户点赞更接近「官方认定的好发言」")
def e2(rec: Record) -> int:
    return int(bool(rec.is_featured))


@metric("E3_ip_region", group="E", name="IP 属地", requires=("ip_region",),
        kind="categorical", note="仅在数据集提供属地时可用；属个人信息，导出前须确认粒度")
def e3(rec: Record) -> str | None:
    return rec.ip_region
