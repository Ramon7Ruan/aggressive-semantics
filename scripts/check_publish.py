#!/usr/bin/env python3
"""报告公开前的合规闸门。

`report/*.md` 要进公开仓库，但报告里可能出现三类不该出去的东西：

1. **可定位到个人的信息**——绝对路径、@handle、邮箱、手机号、长数字 ID；
2. **大段逐字引用**——短片段（≤ 15 字）用于让读者审计话题编码是必要的，
   但整句抄录会把原始语料夹带出仓，绕开「原始语料不随仓分发」的承诺；
3. **本地绝对路径**——别人 clone 后照着复现会找不到目录。

这个脚本把上面三条变成可执行的判据。它在 `repro/run_all.sh` 末尾自动跑，
**失败即非零退出**，不会因为报告"看起来没问题"就放过。

用法：
    python scripts/check_publish.py                # 检查 report/*.md
    python scripts/check_publish.py --path docs    # 检查指定目录

退出码：0 通过，1 有违规。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent

# 逐字引用的字符上限。15 字足够表达一个标签（如「写黄书判10年虐童一年半」12 字），
# 又不至于把一句话抄完。改这个数字等于改公开政策，要同步 report/README.md。
MAX_QUOTE = 15

# 引号内的片段（中英文四种引号）
QUOTE_RE = re.compile(r"「([^」]{2,400})」|『([^』]{2,400})』"
                      r"|“([^”]{2,400})”|\"([^\"]{2,400})\"")

PII = {
    "绝对路径": re.compile(r"/Users/[^\s`)|]+"),
    "URL": re.compile(r"https?://\S+"),
    "@handle": re.compile(r"(?<![\w.])@[\w\u4e00-\u9fa5]{2,20}"),
    "长数字ID": re.compile(r"(?<!\d)\d{10,}(?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
}

# 邮箱与 URL 规则会互相命中（a@b.com 也是 URL 之外的东西），报错时合并去重即可
IGNORE_URL_HOSTS = ("github.com/Ramon7Ruan",)

# 占位词。政策文档里必须能写「不含 @handle」这句话，不能因此判自己违规。
# 只放行这几个字面量，不是放宽 @handle 规则本身。
PLACEHOLDER_HANDLES = {"@handle", "@user", "@username", "@someone", "@某人"}


def load_corpus() -> str:
    """把已 prepared 的语料拼成一个字符串，用于判定「是否逐字引用」。

    只在本地有语料时有意义；干净 clone 上这些文件不存在，此时跳过逐字检查
    并明确告知——不要让「检查通过」被误读成「检查过」。
    """
    parts = []
    for name in ("weibo16", "cold", "orchid"):
        p = BASE / "data" / "raw" / "prepared" / f"{name}.jsonl"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    parts.append(json.loads(line).get("text") or "")
                except json.JSONDecodeError:
                    continue
    return "\n".join(parts)


def scan(path: pathlib.Path, corpus: str) -> tuple[list[str], list[str], dict]:
    """返回 (违规, 提示, 统计)。"""
    bad: list[str] = []
    note: list[str] = []
    stats = {"quotes": 0, "verbatim": 0, "longest": 0}

    lines = path.read_text(encoding="utf-8").splitlines()

    for no, line in enumerate(lines, 1):
        for name, pat in PII.items():
            for m in pat.finditer(line):
                hit = m.group(0)
                if name == "URL" and any(h in hit for h in IGNORE_URL_HOSTS):
                    continue
                if name == "@handle" and hit in PLACEHOLDER_HANDLES:
                    continue
                bad.append(f"{path.name}:{no}  {name}  {hit[:60]}")

    for m in QUOTE_RE.finditer(path.read_text(encoding="utf-8")):
        frag = next(g for g in m.groups() if g).strip()
        stats["quotes"] += 1
        stats["longest"] = max(stats["longest"], len(frag))
        if corpus and len(frag) >= 5 and frag in corpus:
            stats["verbatim"] += 1
            if len(frag) > MAX_QUOTE:
                bad.append(f"{path.name}  逐字引用 {len(frag)} 字 > {MAX_QUOTE}"
                           f"  {frag[:40]}…")

    if not corpus:
        note.append(f"{path.name}  无本地语料，跳过逐字引用检查")
    return bad, note, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="报告公开前的合规闸门")
    ap.add_argument("--path", default="report", help="要检查的目录（默认 report）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = BASE / args.path
    files = sorted(root.glob("*.md"))
    if not files:
        print(f"没有找到 {args.path}/*.md，无从检查", file=sys.stderr)
        return 1

    corpus = load_corpus()
    all_bad: list[str] = []
    total = {"quotes": 0, "verbatim": 0, "longest": 0}

    print(f"检查 {len(files)} 份报告（逐字引用上限 {MAX_QUOTE} 字）")
    for f in files:
        bad, note, st = scan(f, corpus)
        all_bad += bad
        for k in total:
            total[k] = max(total[k], st[k]) if k == "longest" else total[k] + st[k]
        flag = "违规" if bad else "ok"
        print(f"  {f.name:24s} 引号 {st['quotes']:3d}  "
              f"逐字 {st['verbatim']:2d}  最长 {st['longest']:2d} 字  {flag}")
        for n in note:
            print(f"      · {n}")

    print(f"\n合计：引号片段 {total['quotes']}，其中逐字命中语料 {total['verbatim']}，"
          f"最长 {total['longest']} 字")

    if all_bad:
        print("\n违规项：", file=sys.stderr)
        for b in all_bad:
            print(f"  - {b}", file=sys.stderr)
        print(f"\n共 {len(all_bad)} 项。改报告或调 MAX_QUOTE 前不要推到公开仓库。",
              file=sys.stderr)
        return 1

    print("通过：无个人标识、无超长逐字引用、无本地绝对路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
