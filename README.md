# discourse-kit

网络社区**社会议题**话语形态的指标与编码方案。数据源无关：换数据集不用改分析代码。

> 定位说明：这不是爬虫项目。核心资产是 **schema + 指标定义 + 编码手册 + 可复现管线**，
> 采集实现只是其中一个可选入口。

## 为什么需要它

常见做法是「抓一批帖子 → 算词频和情感值 → 画几张图」。这样得到的数字无法回答三个问题：
为什么挑这些帖子？指标为什么这样定义？换一个人算会得到同样的结果吗？

本仓库针对这三点做了工程与方法的约束：

| 问题 | 做法 |
|---|---|
| 样本怎么来的 | 数据契约（schema v1）+ 导入映射留档，任何一步可复查 |
| 指标凭什么这么算 | 每个指标声明定义、依赖字段、最小文本长度、失效边界 |
| 能否复现 | 管线离线、无随机性（除显式设定的 bootstrap/置换种子），一键重跑 |
| 算不出来的怎么办 | 记进「可用性台账」，不当 0、不静默省略 |

## 三层结构

```
data/raw/*.csv|jsonl
      │  ingest（嗅探 → 字段映射）
      ▼
  schema v1 ──────────────► store（SQLite，按 doc_id 幂等）
      │                        │
      │                        ├─ quality（语料画像：标点/长度/段落 → 哪些指标根本失效）
      │                        ├─ labeling（T1–T5 话题类型 / G 组攻击性）
      │                        ├─ metrics（A/B/C/D/E/G 六组，40 个指标）
      │                        └─ hypotheses（H1–H8，话题级精确置换检验）
      ▼
  export（CSV） ────────► report（Markdown 报告）
```

关键边界：**分析管线不认识任何具体平台**。平台知识全部收敛在 `adapters/` 与
`data/mappings/`，新增数据源不影响下游任何一行。

## 快速开始

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 0 自检：词典、分词器、指标注册
python -m discourse_kit.cli doctor

# 1 拿到一个陌生数据集/压缩包，先普查（zip 不用解压）
python -m discourse_kit.cli survey data/raw/nbsdc/xxx.zip

# 2 生成一份合成语料（仅用于跑通流程，不是研究数据）
python -m discourse_kit.cli synth --out data/raw/demo --csv

# 3 直接跑（规范 JSONL）
python -m discourse_kit.cli run --file data/raw/demo/units.jsonl

# 3b 用人工话题编码表跑（话题类型无法从主楼推断时必须给）
python -m discourse_kit.cli run --file data/raw/prepared/weibo16.jsonl \
    --topics data/mappings/weibo16_topics.yaml \
    --out data/processed/weibo16 --report report/weibo16_report.md

# 4 接入真实数据集：先看结构，再写映射
python -m discourse_kit.cli sniff data/raw/你的数据集.csv --write-mapping data/mappings/mine.yaml
python -m discourse_kit.cli run --file data/raw/你的数据集.csv --mapping data/mappings/mine.yaml
```

`survey` 与 `sniff` 是两个不同问题：前者回答「这一堆文件里该看哪个」，
后者回答「这个文件有哪些列、能不能对上 schema」。几千个文件的包先跑 `survey`。

产物在 `data/processed/`（`units.csv` / `metrics.csv` / `threads.csv` /
`metric_coverage.csv` / `labels.csv` / `codebook.json`）与 `report/analysis_report.md`。

## 数据契约（schema v1）

必填 4 个字段，其余 20 个可选。**可选字段缺席不是错误**，而是「该指标算不出来」，
会被记进台账。

| 必填 | 含义 |
|---|---|
| `doc_id` | 单元唯一 ID |
| `thread_id` | 所属话题/帖 |
| `unit` | `opening`（主楼）/ `reply`（回复）/ `argument`（论据） |
| `text` | 正文 |

可选字段含 `floor_no`、`author_id`、`posted_at`、`has_quote`、`stance`、
`argument_type`、`ip_region`、`reply_count` 等，详见 `discourse_kit/schema.py`。

## 指标

| 组 | 内容 | 代表指标 |
|---|---|---|
| A 参与强度 | 帖内回复、浏览、是否发起者 | `A1_reply_count` |
| B 话语形态 | 词汇丰富度、句长、人称、语域 | `B1_mattr`、`B8_first_person` |
| C 论证结构 | 证据句率、论证密度、让步、引用 | `C6_arg_density` |
| D 立场情绪 | 极性、绝对化、确定性、反讽修正 | `D2_absolutism`、`D5_polarity_adj` |
| E 社群位置 | 等级、精选、属地 | `E2_featured` |
| G 攻击性 | 强度、对象、形式、侮辱密度、群体标签化 | `G1_intensity`、`G2_target` |

四条硬规则：

1. **词汇丰富度用 MATTR，不用 TTR。** TTR 随长度单调下降，跨长度比较必然得出
   「短文本词汇更丰富」的假结论。
2. **短文本不进入长文本指标。** MATTR 需 ≥100 字，论证密度需 ≥80 字；不足则记为缺失。
3. **零膨胀指标必须拆双边际。** 攻击、群体标签化、反讽在单元上有 90%+ 是 0，
   这时 Cohen's d 的分母被大量 0 撑大，恒在 0.1 附近，「差异为零」和「差异很大但稀疏」
   在 d 上长得一样。所以拆成**发生率**（有多少条说了）与**条件强度**（说的人说得多狠）
   两个指标分别检验，并设最小发生率差 5pp、最小相对变化 20%。
4. **攻击性不能用纯词典法下结论。** 中文社区大量「褒义词反用」（睿智/典/孝/急了），
   词典会判为正面。G 组数值应视为**下界**，须与 LLM/人工标注对照
   （`scripts/eval_cold.py` 提供现成流程）。

## 推断单位：话题，不是帖子

这是本仓库与方法学上最容易做错的地方。把 14,705 条评论当独立样本做检验，会把
0.1 个百分点的差异推成 p < 0.001——同一话题下的回复共享议题、共享词汇、共享情绪，
并不独立，这叫伪重复（pseudoreplication）。

本仓库的做法：

- **抽样单位是话题**。每个话题先聚合成一个值（默认均值，不用中位数——零膨胀指标
  的中位数恒为 0，16 个话题全是 0 时任何检验都退化成「0 与 0 无差异」）。
- **检验用精确置换**。6 vs 10 的分组只有 C(16,6)=8008 种可能，全部穷举，
  p 是精确值，不依赖分布假设，也不需要随机种子。
- **同时报告两个 p**。`p(话题)` 是可引用的那个；`p(单元)` 只作参考，
  并明确标注它在什么情况下会误导。
- **先做方差分解再谈组间差异**。用 ICC(1) 分别按话题、按类型分组，
  回答「话题是否真的解释了发言差异」。若话题的 ICC 远高于类型，
  说明真正起作用的是具体议题，此时「类型不影响话语形态」这种结论不能下。

## 话题编码

`T1` 解释型 / `T2` 求助型 / `T3` 制度指向 / `T4` 抉择型 / `T5` 行动者指向（指向不在场第三方），
外加 `T?` 无法归类。二维依据是**争议性（共识/争议）× 指向性（社会现象/自身处境/第三方）**。

当前研究只比较 **T3 vs T5**（制度指向 vs 行动者指向）。这两类的差别在于「话是说给谁听的」：
T3 的争点在政策/制度本身（该不该立法、尺度是否过度），T5 的争点在某个具体的人、群体、
机构或国家。T1/T2/T4 在现有语料里为空，代码保留以便换平台时启用。

话题类型是**给定分组，由人工逐话题判定**，写在 `data/mappings/*_topics.yaml` 里，
含编码、子型、置信度、争论焦点与判定依据，通过 `--topics` 传入。管线会做覆盖校验：
语料里出现但编码表没写的话题会让管线**直接失败**，因为漏一个就会让该组静默消失。

仓库内的规则标注器只是**先验**（用于分层抽样与对照），不是最终标签。
正式使用前必须先做人工金标准，κ ≥ 0.70 才可通过（`discourse_kit/labeling/agreement.py`）。

## 语料质量闸门

指标「算得出来」不等于「算得有意义」。管线先算语料画像（长度分布、标点率、段落率），
再逐指标检查其语料前提：

| 前提 | 门槛 | 不满足时 |
|---|---|---|
| `sentence`（能分句） | 含句末标点 ≥ 50% | 句长、论证密度、问句占比等 7 个指标整列弃用 |
| `paragraph`（有段落） | 含换行 ≥ 10% | 段落类指标弃用 |
| `punctuation` | 含标点 ≥ 50% | 仅告警 |

被弃用的指标在台账里记为 `invalid_for_corpus`，**不是 0，也不是缺失**。
这是为了挡住最常见的一类错误：在去标点的微博语料上算出「平均句长 16 字」并当成结论。

## 双编码信度

话题编码与攻击性判定都是人工产物。没有一致性系数，就无法区分
「这是数据的性质」与「这是编码者的习惯」。流程是三条命令：

```bash
# 1 抽样：三层（群体指称 / 攻击阳性 / 阴性），按话题配额，固定种子
python scripts/make_kappa_sample.py
#    → data/annotation/kappa_sample.jsonl
#    → data/annotation/annotate.html（离线单文件标注页，双击打开）

# 2 两名编码者各自独立标注，各导出一份 CSV（id,target）

# 3 算 κ、混淆矩阵、逐类 F1，并与词表对照
python scripts/kappa_report.py --a data/annotation/kappa_sheet_A.csv \
                               --b data/annotation/kappa_sheet_B.csv
#    → report/kappa.md

# 4 只有 A 份时也能跑：此时没有信度，只剩「人 vs 词表」的效度检验
python scripts/kappa_report.py --a data/annotation/kappa_sheet_A.csv
# 5 用人工标注对 G1 做分层加权校准（中文语料上的独立校准）
python scripts/calibrate_g1.py
#    → report/g1_calibration.md
```

三点设计上的考虑：

- **为什么按阳性/阴性分层**：G1>0 只占 4%。纯随机抽样时两名编码者只要把
  少数阳性都判成 none，κ 也会接近 1——那是 kappa paradox，测的是「都不认为有攻击」。
- **为什么要单独抽「群体指称」层**：词表在 weibo16 上只判出 8 条 group（0.05%），
  而人工一眼能看出「韩国人都这样」。不主动抽这一层，group 类的 κ 会宽到没有意义。
- **为什么「拿不准」不进分母**：硬猜出来的标注会让 κ 看起来更低，但那是噪声不是分歧。
  不确定率超过 15% 应当补规则重编，而不是硬着头皮算。
- **「攻击观点」不能与「攻击个人/群体」并列**：实测把 opinion 也算作攻击时，
  人工基准率冲到 59.8%——14,705 条社会议题评论里六成「有攻击」，这个数不可信，
  因为该类吞进了「支持抵制」「坚决抵制」这类纯表态。若要用 `G2_target`，
  只保留 person / group，opinion 的语义交回 D 组（立场表达强度）。
  详见 `report/g1_calibration.md`。

编码手册见 `docs/编码手册_攻击对象.md`。门槛写在 `config.MIN_KAPPA`（0.70）。

## 测试

```bash
pytest -q            # 151 个测试，覆盖 schema/规范化/导入/普查/指标/统计/端到端
pytest -m live       # 可选：4 项真实网络连通性与合规检查（需自备抓取环境）
bash repro/run_all.sh  # 端到端复现：三份语料 + 交叉检验，产出全部报告
```

## 数据与合规

- 原始语料**不入仓**。仓内只含合成样本与人工改写示例。
- 作者标识一律加盐哈希；`export` 默认 `text_mode=truncate`，对外发布用 `drop`。
- 若自行采集：先读目标站点 `robots.txt` 并用 `urllib.robotparser` 判定（不要肉眼扫），
  只抓公开页面、单线程、低频、不绕过任何技术措施。

## License

代码 Apache-2.0；文档 CC BY 4.0；数据不随仓分发。
