"""平台适配层契约。

这是整个项目最重要的一条边界：**分析管线不认识任何具体平台，只认下面这个 schema。**

新增一个平台 = 新增一个实现 Adapter 的文件，下游一行不用改。
"""

from typing import Protocol, TypedDict, runtime_checkable


class ThreadStub(TypedDict):
    """列表页的一条帖子摘要。字段是 schema v1 的最小集。"""

    pid: str            # 平台内唯一 ID
    board_id: str       # 所属版块
    title: str
    author: str         # 原始昵称，入库前须哈希
    time_raw: str       # 平台原始时间字符串，由 normalizer 统一
    reply_count: int | None
    view_count: int | None


class ThreadMeta(TypedDict):
    """详情页的主楼元信息。"""

    pid: str
    board_id: str
    title: str
    author: str
    created_at_raw: str
    text: str
    char_count: int
    para_count: int
    ip_region: str | None


class FloorRow(TypedDict):
    """一条楼层（含主楼）。"""

    floor_no: int
    author: str
    user_level: int | None
    is_op: bool
    posted_at_raw: str
    ip_region: str | None
    text: str
    char_count: int
    para_count: int
    has_quote: bool
    quote_text: str | None
    is_featured: bool


@runtime_checkable
class Adapter(Protocol):
    """平台适配器。

    实现者只需回答"URL 怎么拼"和"HTML 怎么解析"这两个问题，
    其余（限速、重试、存储、指标）全部由通用层负责。
    """

    name: str

    def list_url(self, board_id: str, page: int) -> str:
        """版块列表页 URL。page 从 1 开始。"""
        ...

    def thread_url(self, pid: str, page: int) -> str:
        """帖子详情页 URL。page 从 1 开始。"""
        ...

    def parse_board(self, html: str, board_id: str) -> list[ThreadStub]:
        """解析列表页。

        解析出 0 条时**必须**抛 ParseError —— 那意味着页面结构变了，
        而不是"这一页恰好是空的"。
        """
        ...

    def parse_thread(self, html: str) -> tuple[ThreadMeta, list[FloorRow]]:
        """解析详情页，返回 (主楼元信息, 楼层列表)。

        同上：0 层必须抛 ParseError。
        """
        ...
