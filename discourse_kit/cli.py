"""命令行入口。

设计取向：每条子命令都对应管线的一个可单独验收的环节，
`run` 只做编排。这样出问题时能定位到「哪一步」，而不是「跑挂了」。

    python -m discourse_kit.cli sniff data/raw/xxx.csv
    python -m discourse_kit.cli survey data/raw/nbsdc.zip
    python -m discourse_kit.cli synth --out data/raw/demo
    python -m discourse_kit.cli run --file data/raw/demo/units.jsonl
    python -m discourse_kit.cli doctor
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .ingest.sniff import format_report, sniff_file
from .metrics.lexicon import all_lexicons


def cmd_sniff(args) -> int:
    info = sniff_file(args.file, rows=args.rows)
    print(format_report(info))
    if args.json:
        print("\n" + json.dumps(info, ensure_ascii=False, indent=2)[:4000])
    if args.write_mapping:
        from .ingest.table import write_mapping

        m = {
            "source": args.source or Path(args.file).stem,
            "file": str(args.file),
            "encoding": "auto",
            "delimiter": "auto",
            "unit_default": args.unit_default,
            "fields": dict(info.get("suggested_mapping", {})),
        }
        p = write_mapping(args.write_mapping, m)
        print(f"\n建议映射已写入 {p}（请人工核对后再用）")
    return 0


def cmd_survey(args) -> int:
    from .ingest.survey import format_survey, survey

    info = survey(args.path, top=args.top, probe=args.probe)
    text = format_survey(info)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


def cmd_prepare(args) -> int:
    """把公开数据集转成 schema v1 JSONL。只写文件，不碰数据库。"""
    import sys as _sys

    _ROOT = Path(__file__).resolve().parent.parent
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
    from adapters.datasets import REGISTRY, prepare

    if args.dataset not in REGISTRY:
        print(f"未知数据集 {args.dataset!r}，可用：{sorted(REGISTRY)}")
        return 2
    rows = prepare(args.dataset, args.path)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_thr = len({r["thread_id"] for r in rows})
    print(f"{args.dataset}: {len(rows)} 条单元 → {n_thr} 个话题 → {out}")
    return 0


def cmd_synth(args) -> int:
    from .ingest import synth

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = synth.generate(seed=args.seed)
    p1 = synth.write_jsonl(str(out / "units.jsonl"), rows)
    print(f"已生成 {len(rows)} 条单元 → {p1}")
    if args.csv:
        p2 = synth.write_csv_messy(str(out / "units_raw.csv"), rows)
        print(f"已生成脏表头 CSV（用于测试映射导入）→ {p2}")
    return 0


def cmd_run(args) -> int:
    from .pipeline import run

    res = run(path=args.file, mapping=args.mapping,
              db_path=args.db, out_dir=args.out, report_path=args.report,
              topic_labels=args.topics,
              text_mode=args.text_mode, limit=args.limit,
              source_note=args.source_note)
    print(f"导入 {res.n_rows_in} 行 → 合法单元 {res.n_records or res.counts.get('units', 0)} 条")
    if res.n_schema_errors:
        print(f"schema 错误 {res.n_schema_errors} 条（示例：{res.errors[:2]}）")
    for key, val in res.counts.items():
        print(f"  {key:10s} {val}")

    c = res.corpus
    print(f"\n语料画像：中位 {c.median_chars:.0f} 字 ｜ 含标点 {c.punctuation_rate:.1%} ｜ "
          f"含句末标点 {c.sentence_end_rate:.1%} ｜ ≤10 字 {c.short_share_10:.0%}")

    print("\n质量检查：")
    for i in res.issues:
        print(f"  [{i['level']}] {i['code']}: {i['message']}")

    bad = res.ledger[res.ledger["status"] != "ok"]
    print(f"\n指标：{len(res.ledger) - len(bad)}/{len(res.ledger)} 个产出有效值")
    for _, r in bad.iterrows():
        print(f"  [{r['status']}] {r['key']}: {r['detail'] or r['status']}")

    ok_hyp = res.hyp[res.hyp["status"] == "ok"] if "status" in res.hyp else res.hyp
    print(f"\n假设：{len(ok_hyp)}/{len(res.hyp)} 条可检验")
    for _, r in ok_hyp.iterrows():
        print(f"  {r['code']}: {r['verdict']}")

    print("\n产出：")
    for k, v in res.paths.items():
        print(f"  {k:10s} {v}")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    print("词典：")
    for k, v in all_lexicons().items():
        flag = "ok " if v else "空!"
        if not v:
            ok = False
        print(f"  {k:14s} {v:4d} 条  {flag}")
    from .normalize import tokenizer_name

    print(f"\n分词器：{tokenizer_name()}")
    print(f"数据库：{config.DB_PATH}  (存在：{config.DB_PATH.exists()})")
    from .metrics.compute import compute_records
    from .metrics.registry import all_specs

    print(f"指标注册：{len(all_specs())} 个")
    from .ingest import synth
    from .schema import coerce

    rows = synth.generate(seed=1)[:30]
    recs = [coerce(r) for r in rows]
    df, ledger = compute_records(recs)
    print(f"自检：{df.shape[0]} 行 × {df.shape[1]} 指标，"
          f"可用 {int((ledger['status'] == 'ok').sum())} 个")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="discourse-kit",
                                description="网络社区话语形态分析工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sniff", help="诊断一个陌生数据文件的结构")
    s.add_argument("file")
    s.add_argument("--rows", type=int, default=200)
    s.add_argument("--json", action="store_true", help="同时打印完整 JSON")
    s.add_argument("--write-mapping", help="把建议映射写到该 YAML 路径")
    s.add_argument("--source", help="数据集标识（写入 units.source）")
    s.add_argument("--unit-default", default="argument",
                   help="文件无 unit 列时的默认单元类型")
    s.set_defaults(func=cmd_sniff)

    s = sub.add_parser("survey", help="普查目录/压缩包：哪几个文件装着文本")
    s.add_argument("path", help="目录、.zip 或 .tar.*")
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--probe", type=int, default=0,
                   help="对最大的 N 个表文件直接跑 sniff（需已解压）")
    s.add_argument("--out", help="把普查结果写成 Markdown")
    s.set_defaults(func=cmd_survey)

    s = sub.add_parser("prepare", help="把公开数据集转成 schema v1 JSONL")
    s.add_argument("--dataset", required=True, help="weibo16 / cold")
    s.add_argument("--path", required=True, help="数据集目录")
    s.add_argument("--out", required=True, help="输出 JSONL 路径")
    s.set_defaults(func=cmd_prepare)

    s = sub.add_parser("synth", help="生成合成语料（仅用于测试/演示）")
    s.add_argument("--out", default=str(config.RAW_DIR / "demo"))
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--csv", action="store_true", help="同时生成脏表头 CSV")
    s.set_defaults(func=cmd_synth)

    s = sub.add_parser("run", help="导入 → 指标 → 导出 → 报告")
    s.add_argument("--file", required=True)
    s.add_argument("--mapping", help="表格文件必填")
    s.add_argument("--topics", help="人工话题编码表 YAML（给定分组；缺省用规则标注）")
    s.add_argument("--db")
    s.add_argument("--out", help="导出目录")
    s.add_argument("--report", help="报告输出路径")
    s.add_argument("--text-mode", default="truncate",
                   choices=["keep", "truncate", "drop"])
    s.add_argument("--limit", type=int)
    s.add_argument("--source-note", default="")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("doctor", help="自检：词典、分词器、指标注册")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
