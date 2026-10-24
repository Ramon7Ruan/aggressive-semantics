# 报告目录

这里是从代码直接生成的实证结果，**不是手写的结论**。每份报告都能用一条命令重建。

```
bash repro/run_all.sh        # 全部重建，约 6 分钟，无网络
```

## 一、每份报告是什么

| 文件 | 内容 | 怎么来的 |
|---|---|---|
| `weibo16_report.md` | 主分析：40 个指标在 14,705 条微博上的覆盖与分布、9 条假设检验、ICC、话题级置换检验 | `bin/dk run --topics data/mappings/weibo16_topics.yaml` |
| `cold_report.md` | COLD（英文 Twitter 仇恨言论）同套代码的对照 | `bin/dk run --file .../cold.jsonl` |
| `orchid_report.md` | OrChiD（印度新闻评论）同套代码的对照 | `bin/dk run --file .../orchid.jsonl` |
| `corpus_comparison.md` | 三份语料同一套代码下的指标可算性对照 —— **核心论证之一** | `scripts/compare_corpora.py` |
| `sensitivity.md` | 把两个 `medium` 置信度的话题从 T3 改判 T5 后重跑，检验结论是否翻转 | `scripts/sensitivity_check.py` |
| `kappa.md` | 人工标注（246 条）与词表的对照 + 边际分布诊断 | `scripts/kappa_report.py` |
| `g1_calibration.md` | 中文语料上 G1 的分层加权校准（宽/严两个口径） | `scripts/calibrate_g1.py` |
| `analysis_report.md` | 合成假数据的 demo 报告，用于验证管线本身 | `bin/dk run --file data/raw/demo/units.jsonl` |
| `cold_eval.json` | COLD 金标准上的 G 组精确率/召回率/AUC（报告从中读数字，不硬编码） | `scripts/eval_cold.py` |

## 二、为什么这些报告可以公开

原始语料不随仓分发（见主 README），但这些报告里**必然出现少量原文片段**——
比如要说明「红黄蓝这个话题为什么判 T3」，得举一句实际发言。所以公开政策是：

| 规则 | 判据 |
|---|---|
| 逐字引用上限 **15 字** | 够表达一个标签（最长的一处 12 字），又不至于抄完一整句 |
| 不含 @handle、邮箱、手机号、长数字 ID | 报告里一处都没有 |
| 不含本地绝对路径 | 别人 clone 后照着跑不会踩空 |

**这三条不是承诺，是 `scripts/check_publish.py` 的检查项**，在 `repro/run_all.sh`
末尾自动执行，违规即非零退出。想核对就自己跑：

```bash
python scripts/check_publish.py
```

它会打出每份报告的引号片段数、其中逐字命中语料的有几条、最长多少字。
在干净 clone 上没有语料，逐字检查会被跳过并**明确提示**——
不要让「通过」被误读成「检查过」。

## 三、读这些报告时最容易读错的三处

1. **`p(单元)` 不能引用。** 报告并列 `p(单元)` 与 `p(话题)`，前者把 14,705 条当独立观测，
   是伪重复，`p<0.001` 是假的。可引用的只有 `p(话题)`（6v10 穷举 `C(16,6)=8008` 种分组的精确值）。
2. **G 组数字是下界，不是估计。** 中文语料上严口径召回 12.4%、精确率 0.343；
   英文 COLD 上召回 21.3%、精确率 0.863。两组不能混引，引用时必须注明语料与口径。
3. **`kappa.md` 的第三节是效度，不是信度。** 本批只有一名编码者，所以**没有编码者间信度**。
   两人一致（κ 高）才叫信度，人机一致只能说明词表准不准。
