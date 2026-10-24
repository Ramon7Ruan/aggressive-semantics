"""编码手册（codebook）：五类话题 + G 组维度的定义。

这份文件是**研究设计的核心资产**，比代码重要。
开源仓库里别人真正想复用的是它，不是爬虫。

两条纪律：
1. 定义一旦定稿，实测中不得因为「编不出来」而回头改措辞——那等于用数据定假设。
   要改就整体改版并重标，且记录改了什么。
2. 每类必须给出「排他条件」：什么情况下**不算**这一类。
   没有排他条件的分类体系，编码者会各按直觉，κ 永远上不去。
"""

from __future__ import annotations

TOPIC_TYPES: dict[str, dict] = {
    "T1": {
        "name": "解释型",
        "controversy": "共识",
        "target": "社会现象",
        "definition": "询问某一社会现象为何存在、如何形成，回答者提供因果解释。"
                      "话题本身不要求站边，争论的是「原因是什么」而非「对不对」。",
        "include": ["为什么……", "是什么原因导致……", "如何解释……", "为何……", "……的成因"],
        "exclude": ["带有明确「应不应该」的规范判断（→T3）",
                    "以个人选择为落点（→T4）",
                    "以评价某个具体人或群体为落点（→T5）"],
        "example": "为什么年轻人越来越不愿意结婚",
    },
    "T2": {
        "name": "求助型",
        "controversy": "共识",
        "target": "自身处境",
        "definition": "发帖者为自己的具体处境寻求信息或建议，答案是可操作的。"
                      "争议性低，因为目标不是说服别人而是解决自己的问题。",
        "include": ["请问……怎么办", "求建议", "该注意什么", "有没有人知道", "我遇到……"],
        "exclude": ["涉及价值抉择且双方可争论（→T4）",
                    "实质是在评价他人（→T5）"],
        "example": "第一次租房该注意哪些坑",
    },
    "T3": {
        "name": "立场型",
        "controversy": "争议",
        "target": "社会现象",
        "definition": "就某项政策、事件或社会安排提出「应不应该 / 好不好」的规范判断，"
                      "指向抽象对象，参与者分成可辨识的立场阵营。",
        "include": ["如何看待……", "怎么评价……", "应不应该……", "……是否合理", "支持还是反对"],
        "exclude": ["落点是「我该怎么办」（→T4）",
                    "落点是某个人/群体的品行或能力（→T5）"],
        "example": "如何看待某市对外地车牌的限制",
    },
    "T4": {
        "name": "抉择型",
        "controversy": "争议",
        "target": "自身处境",
        "definition": "发帖者面临两难的个人选择，征求他人意见。"
                      "争议性来自价值排序不同，但因为落点是具体个人，讨论容易转为劝告。",
        "include": ["我该不该……", "要不要……", "选哪个", "纠结……", "我是不是应该……"],
        "exclude": ["已有明确事实性求助（→T2）",
                    "讨论的是别人的选择（→T5c 行为审判）"],
        "example": "我该不该为了照顾父母放弃现在的工作",
    },
    "T5": {
        "name": "审判型",
        "controversy": "争议",
        "target": "不在场的第三方",
        "definition": "对某个不在场的人、群体或行为作出评价与裁决。"
                      "关键特征不是「有攻击性」（那是结果），而是**评价对象缺席**："
                      "被讨论者无法回应，因此讨论容易滑向标签化。",
        "include": ["这人……行不行", "某某的能力/人品", "……地方的人是不是都这样",
                    "他这么做对不对", "这种……算什么"],
        "exclude": ["对象是抽象制度或政策（→T3）",
                    "发帖者本人是评价对象（→T4）"],
        "example": "某主播的学历是不是造假",
        "subtypes": {
            "T5a": "人物审判：指向具体个人",
            "T5b": "群体审判：指向某类人（地域/性别/职业/年龄）",
            "T5c": "行为审判：指向某个具体行为是否正当",
        },
    },
}

#: 允许的「无法归类」取值。必须存在——强行归类会虚高 κ。
UNKNOWN = "T?"

AGGRESSION_TARGETS = {
    "none": "无攻击对象",
    "opinion": "攻击观点/论证",
    "person": "攻击具体个人",
    "group": "攻击群体",
}

AGGRESSION_FORMS = {
    "none": "无",
    "insult": "侮辱词",
    "labeling": "群体标签化",
    "derogation": "贬损（无侮辱词）",
    "irony": "反讽/褒词反用",
    "rhetorical": "反问施压",
    "threat": "威胁",
}


def type_of(code: str) -> dict | None:
    return TOPIC_TYPES.get(code[:2]) if code else None


def label_set() -> list[str]:
    return list(TOPIC_TYPES) + [UNKNOWN]


def describe() -> str:
    """渲染成人可读的手册摘要（写进报告附录）。"""
    lines = ["# 话题类型编码手册（摘要）", ""]
    for code, d in TOPIC_TYPES.items():
        lines.append(f"## {code} {d['name']}（{d['controversy']} × {d['target']}）")
        lines.append(d["definition"])
        lines.append(f"- 纳入：{'；'.join(d['include'])}")
        lines.append(f"- 排除：{'；'.join(d['exclude'])}")
        lines.append(f"- 例：{d['example']}")
        if "subtypes" in d:
            for k, v in d["subtypes"].items():
                lines.append(f"  - {k}：{v}")
        lines.append("")
    lines.append(f"## {UNKNOWN} 无法归入以上五类")
    lines.append("保留该取值：强行归类会抬高一致率而掩盖分类体系的缺陷。")
    return "\n".join(lines)
