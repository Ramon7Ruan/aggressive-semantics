"""LLM 辅助标注：接口 + 缓存 + 校验，不含任何默认网络调用。

为什么这么设计：
1. **默认不发请求。** 仓库被 fork 后不该因为跑一次 `--all` 就产生费用。
   客户端由调用方注入，key 不走配置文件、不进仓库。
2. **结果必须落盘缓存。** 同一 doc_id 同版本提示词只请求一次，
   否则「重跑得到不同标签」会毁掉可复现性。
3. **输出必须强校验。** 模型返回的标签不在允许集合内时丢弃并记账，
   绝不做「猜一个最接近的」——那会污染一致性统计。

用法：
```python
from discourse_kit.labeling import llm_labeler as llm
client = llm.OpenAICompatClient(base_url=..., api_key=..., model=...)
llm.label_records(records, client, cache_path="data/processed/llm_cache.jsonl")
```
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable, Protocol

from ..schema import UNIT_OPENING, Record
from .codebook import AGGRESSION_FORMS, AGGRESSION_TARGETS, TOPIC_TYPES, UNKNOWN

PROMPT_VERSION = "v1"

SYSTEM = (
    "你是社会科学研究的内容编码员。你的任务是把给定的中文网络帖子按既定手册归类。"
    "严格只依据用户给出的手册与文本，不做外部推测。"
    "输出必须是单个 JSON 对象，不要 markdown 代码块，不要解释。"
)

TEMPLATE = """## 编码手册
{codebook}

## 待编码文本
标题：{title}
正文：{text}

## 任务
1. topic_type：从 {codes} 中选一个；无法归入任何一类填 {unknown}。
   判定顺序：先看落点指向谁（社会现象 / 自身处境 / 不在场第三方），再看有无规范判断。
2. confidence：0–1，表示你对 topic_type 判定的确信程度。
3. rationale：一句话说明依据（不超过 40 字）。

只输出 JSON：{{"topic_type": "...", "confidence": 0.0, "rationale": "..."}}"""

AGGRESSION_TEMPLATE = """## 攻击性编码
对象取值：{targets}
形式取值：{forms}

## 文本
{text}

## 任务
判断该文本是否包含攻击性表达。注意「褒义词反用」（如「睿智」「典」「孝」「急了」
在中文社区常作贬义）——字面正面但语用贬损的，应按反讽计。
强度 0=无攻击，1=轻微不满，3=明确贬损，5=直接辱骂或群体污名。

只输出 JSON：{{"intensity": 0, "target": "none", "form": "none"}}"""


class LabelClient(Protocol):
    def __call__(self, system: str, user: str) -> str: ...


def build_prompt(rec: Record, codebook_text: str) -> str:
    return TEMPLATE.format(
        codebook=codebook_text,
        title=(rec.topic_title or "").strip() or "（无）",
        text=rec.text[:1500],
        codes="/".join(TOPIC_TYPES) ,
        unknown=UNKNOWN,
    )


def _cache_key(rec: Record, prompt: str) -> str:
    h = hashlib.sha1(f"{PROMPT_VERSION}|{rec.doc_id}|{prompt}".encode()).hexdigest()
    return h[:16]


def _load_cache(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            out[o["key"]] = o
    return out


def parse_json_response(raw: str) -> dict | None:
    """容忍模型偶尔加上 ``` 包裹的情况，其余一律当失败。"""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s.split("\n", 1)[1] if "\n" in s else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return None


def label_records(records: Iterable[Record], client: LabelClient, *,
                  cache_path: str | Path = "data/processed/llm_cache.jsonl",
                  codebook_text: str | None = None,
                  only_openings: bool = True,
                  limit: int | None = None) -> tuple[list[tuple], list[str]]:
    """返回 (labels 行, 错误列表)。labels 行为 (doc_id, dimension, label, score, method)。"""
    from .codebook import describe

    cb = codebook_text or describe()
    cache_p = Path(cache_path)
    cache_p.parent.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(cache_p)

    rows: list[tuple] = []
    errors: list[str] = []
    todo = [r for r in records if (r.unit == UNIT_OPENING if only_openings else True)]
    if limit:
        todo = todo[:limit]

    with cache_p.open("a", encoding="utf-8") as fh:
        for rec in todo:
            prompt = build_prompt(rec, cb)
            key = _cache_key(rec, prompt)
            hit = cache.get(key)
            if hit is None:
                try:
                    raw = client(SYSTEM, prompt)
                except Exception as exc:
                    errors.append(f"{rec.doc_id}: 请求失败 {exc!r}")
                    continue
                obj = parse_json_response(raw)
                if obj is None:
                    errors.append(f"{rec.doc_id}: 返回非 JSON：{raw[:80]!r}")
                    continue
                cache[key] = {"key": key, "doc_id": rec.doc_id, "prompt_version": PROMPT_VERSION,
                              "raw": raw, "obj": obj}
                fh.write(json.dumps(cache[key], ensure_ascii=False) + "\n")
                fh.flush()
                hit = cache[key]

            obj = hit["obj"]
            code = str(obj.get("topic_type", "")).strip()
            allowed = set(TOPIC_TYPES) | {UNKNOWN}
            if code not in allowed:
                errors.append(f"{rec.doc_id}: 标签越界 {code!r}，已丢弃")
                continue
            try:
                conf = float(obj.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            rows.append((rec.doc_id, "topic_type", code, conf, "llm"))
    return rows, errors


def export_gold_template(records: Iterable[Record], path: str | Path, *,
                         n: int = 200, seed: int = 42,
                         field_name: str = "human_label") -> Path:
    """导出人工标注表（随机抽样，供双人独立编码）。

    刻意不做预填：预填规则标签会让编码者被锚定，κ 会虚高。
    """
    import random

    openings = [r for r in records if r.unit == UNIT_OPENING]
    random.Random(seed).shuffle(openings)
    pick = openings[:n]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"doc_id": "doc_id", "title": "title", "text": "text",
                             field_name: "T1/T2/T3/T4/T5/T?"},
                            ensure_ascii=False) + "\n")
        for r in pick:
            fh.write(json.dumps({"doc_id": r.doc_id,
                                 "title": r.topic_title or "",
                                 "text": r.text[:300],
                                 field_name: ""}, ensure_ascii=False) + "\n")
    return p


class OpenAICompatClient:
    """OpenAI 兼容接口的最小实现（DeepSeek / 通义 / vLLM 均适用）。

    只有显式构造才会联网；默认不实例化。
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.0, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def __call__(self, system: str, user: str) -> str:  # pragma: no cover - 需真实网络
        import requests

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json={"model": self.model, "temperature": self.temperature,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
