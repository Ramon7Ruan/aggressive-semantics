"""导入层：把「任意形状的数据集」转成 schema v1。

这是 v2 架构里替代原采集器的位置。三件事必须分开：

1. `sniff`   —— 我不知道这个文件长什么样，先给我看结构
2. `table`   —— 我知道列名了，按映射表抽字段
3. `jsonl`   —— 已经是标准格式，直接读

分开的理由：真实数据集第一次拿到时，列名永远和预期不一样。
如果只有一个「导入」入口，第一次失败就只报一句「导入失败」，
而真正需要的信息是「你有 37 列，其中我认出 6 列，缺 2 个必填字段」。
"""

from .table import load_mapping, import_table
from .jsonl import import_jsonl
from .sniff import sniff_file, suggest_mapping

__all__ = [
    "load_mapping",
    "import_table",
    "import_jsonl",
    "sniff_file",
    "suggest_mapping",
]
