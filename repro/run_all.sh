#!/usr/bin/env bash
# 一键复现：从原始数据到全部报告。不含任何网络请求。
#
#   bash repro/run_all.sh                  # 跑全部三份语料（默认）
#   DATA=... MAPPING=... TOPICS=... bash repro/run_all.sh   # 只跑指定的一个文件
#
# 设计意图：审稿人/合作者拿到仓库后，只需要这一条命令就能得到同样的表与报告。
# 三份语料跑的是**同一套代码**，因此「指标能不能算」的差异只可能来自语料形态，
# 这正是本研究的核心论证之一（见 report/corpus_comparison.md）。
# 末尾三份交叉检验各自回答一个问题：
#   cold_eval.json        G 组在人工金标准上值多少（信度）
#   corpus_comparison.md  同一套指标在不同语料上能算出什么（效度）
#   sensitivity.md        换一套话题编码，结论会不会变（稳健性）
set -euo pipefail

cd "$(dirname "$0")/.."

# 解释器选择：显式 PY > .venv > python3
# 不能直接默认 python3：本项目依赖 pandas/yaml，系统 python 通常没有。
_pick_py() {
  if [ -n "${PY:-}" ] && [ -x "${PY}" ]; then echo "$PY"; return; fi
  if [ -x ".venv/bin/python" ]; then echo ".venv/bin/python"; return; fi
  echo "python3"
}
PY="$(_pick_py)"
$PY -c "import pandas, yaml" 2>/dev/null || {
  echo "解释器 $PY 缺依赖，先执行：$PY -m pip install -r requirements.txt" >&2
  exit 1
}
echo "解释器：$PY"

DATA="${DATA:-}"
MAPPING="${MAPPING:-}"
TOPICS="${TOPICS:-}"

echo "== 0/4 自检"
$PY -m discourse_kit.cli doctor

if [ -n "$DATA" ]; then
  echo "== 1/4 单文件模式：$DATA"
  args=(run --file "$DATA")
  [ -n "$MAPPING" ] && args+=(--mapping "$MAPPING")
  [ -n "$TOPICS" ] && args+=(--topics "$TOPICS")
  $PY -m discourse_kit.cli "${args[@]}" \
      --out data/processed --report report/analysis_report.md
else
  echo "== 1/4 生成 schema v1 中间文件"
  mkdir -p data/raw/prepared
  [ -d data/raw/weibo16 ] && $PY -m discourse_kit.cli prepare \
      --dataset weibo16 --path data/raw/weibo16 --out data/raw/prepared/weibo16.jsonl
  [ -d data/raw/cold ] && $PY -m discourse_kit.cli prepare \
      --dataset cold --path data/raw/cold --out data/raw/prepared/cold.jsonl
  [ -f data/raw/orchid/OrChiD.json ] && $PY -m discourse_kit.cli prepare \
      --dataset orchid --path data/raw/orchid --out data/raw/prepared/orchid.jsonl

  echo "== 2/4 主分析：weibo16（T3 vs T5）"
  if [ -f data/raw/prepared/weibo16.jsonl ]; then
    $PY -m discourse_kit.cli run --file data/raw/prepared/weibo16.jsonl \
        --topics data/mappings/weibo16_topics.yaml \
        --db data/processed/weibo16.db --out data/processed/weibo16 \
        --report report/weibo16_report.md
  else
    echo "  跳过：缺 data/raw/weibo16（见 docs/数据集接入指南.md）"
  fi

  echo "== 3/4 金标准与长文本参照"
  [ -f data/raw/prepared/cold.jsonl ] && $PY -m discourse_kit.cli run \
      --file data/raw/prepared/cold.jsonl \
      --db data/processed/cold.db --out data/processed/cold \
      --report report/cold_report.md
  [ -f data/raw/prepared/orchid.jsonl ] && $PY -m discourse_kit.cli run \
      --file data/raw/prepared/orchid.jsonl \
      --db data/processed/orchid.db --out data/processed/orchid \
      --report report/orchid_report.md

  echo "== 4/4 交叉检验"
  [ -f data/processed/cold/metrics.csv ] && $PY scripts/eval_cold.py
  [ -f data/processed/weibo16/metrics.csv ] && $PY scripts/compare_corpora.py
  [ -f data/processed/weibo16/metrics.csv ] && $PY scripts/sensitivity_check.py
  # 人工标注产物：只有标注表存在时才跑，缺失时跳过而不报错
  if [ -f data/annotation/kappa_sheet_A.csv ] && [ -f data/processed/weibo16/metrics.csv ]; then
    $PY scripts/kappa_report.py --a data/annotation/kappa_sheet_A.csv --out report/kappa.md
    $PY scripts/calibrate_g1.py
  else
    echo "  跳过 κ 与 G1 校准：缺 data/annotation/kappa_sheet_A.csv（需人工标注）"
  fi
fi

echo "== 校验产物可被读取"
$PY - <<'PY'
import pathlib, sys
import pandas as pd

need = ["units.csv", "metrics.csv", "threads.csv", "metric_coverage.csv"]
roots = [p for p in (pathlib.Path("data/processed") / n
                     for n in ("weibo16", "cold", "orchid"))
         if (p / "metrics.csv").exists()]
roots.append(pathlib.Path("data/processed"))
for base in roots:
    if not (base / "metrics.csv").exists():
        continue
    print(f"  [{base.name}]")
    for name in need:
        p = base / name
        if not p.exists():
            sys.exit(f"{p} 缺失，管线未正常完成")
        df = pd.read_csv(p, encoding="utf-8-sig")
        print(f"    {name:22s} {df.shape[0]:6d} 行 × {df.shape[1]:3d} 列")
        if df.empty:
            sys.exit(f"{p} 为空，管线未正常完成")
for r in ("report/weibo16_report.md", "report/cold_report.md",
          "report/orchid_report.md", "report/corpus_comparison.md",
          "report/sensitivity.md"):
    p = pathlib.Path(r)
    print(f"  报告 {r:34s} {'ok' if p.exists() else '缺失'}")

# 这两份依赖人工标注，有标注才要求它存在
has_ann = pathlib.Path("data/annotation/kappa_sheet_A.csv").exists()
for r in ("report/kappa.md", "report/g1_calibration.md"):
    p = pathlib.Path(r)
    if has_ann:
        print(f"  报告 {r:34s} {'ok' if p.exists() else '缺失'}")
    else:
        print(f"  报告 {r:34s} 跳过（需人工标注）")
PY
echo "完成。"
