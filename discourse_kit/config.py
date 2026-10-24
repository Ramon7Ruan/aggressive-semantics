"""全局配置。

原则：所有可调参数集中在这里，不散落在代码里。改行为改配置，不改逻辑。

v2 变化：数据来源由「在线爬取」改为「离线公开数据集」。
爬虫相关参数保留（未来若要补充自有数据仍可用），但主路径不再触碰网络。
"""

from pathlib import Path

# ── 数据来源 ──────────────────────────────────────────────────────
# 原始数据集放在 data/raw/，由 ingest 子命令按映射导入。
# 主数据源（待 Ramon 从 nbsdc 下载后放入）：
DATASET_PRIMARY = {
    "name": "nbsdc-8sBYreNN",
    "title": "网络内容辩论博弈知识库（哈工大，2.9GB，50 个争议主题）",
    "cstr": "CSTR:16666.11.nbsdc.8sBYreNN",
    "note": "含知乎辩论区/微博话题/豆瓣小组多源，自带立场与论据标签",
}

# ── 路径 ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
LEXICON_DIR = DATA_DIR / "lexicon"
MAPPING_DIR = DATA_DIR / "mappings"
REPORT_DIR = ROOT / "report"

DB_PATH = PROCESSED_DIR / "discourse.db"

# ── 隐私 ──────────────────────────────────────────────────────────
# 作者标识一律哈希。盐值固定，保证同一作者在不同批次里哈希一致。
HASH_SALT = "discourse-kit-v1"

# ── 文本处理 ──────────────────────────────────────────────────────
# MATTR 的滑动窗口（token 数）。低于 2×窗口长度的文本不宜算，见 metrics。
MATTR_WINDOW = 50
MATTR_MIN_TOKENS = 100

# 句子切分后短于此长度的片段并入上一句（避免把「对。」独立成句）
MIN_SENT_CHARS = 2

# ── 统计 ──────────────────────────────────────────────────────────
BOOTSTRAP_N = 2000
PERMUTATION_N = 5000
ALPHA = 0.05
# 效应量低于此值时，报告中禁止使用「更倾向于」这类比较措辞
MIN_EFFECT_FOR_CLAIM = 0.2

# 零膨胀指标的实质差异门槛。
# 为什么另设一套：G1/G5/G7 这类指标在语料里只有个位数百分比非零，
# 单元级 Cohen's d 的分母被大量 0 撑大，d 恒在 0.1 附近，
# 于是「差异为零」与「差异很大但稀疏」在 d 上看起来一样。
# 对这类指标改用**发生率之差**（百分点）与**相对变化**判读。
MIN_RATE_DIFF = 0.05    # 5 个百分点
MIN_REL_CHANGE = 0.20   # 相对变化 20%

# 标注一致性门槛。低于此值，该编码维度作废，不得进入假设检验。
MIN_KAPPA = 0.70
MIN_LABEL_F1 = 0.80

# ── 在线采集（备用路径，默认关闭）────────────────────────────────
# 2026-10 审计：虎扑/知乎/微博/小红书 robots 全站禁止；豆瓣需登录态。
# 保留这些参数只是为了让 fetcher 仍可被复用与测试，不代表会去抓。
ENABLE_LIVE_FETCH = False
BASE_URL = "https://bbs.hupu.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9"
GAP_MIN = 1.5
GAP_MAX = 2.5
TIMEOUT = 20
MAX_RETRY = 3
BATCH_SIZE = 500
BATCH_PAUSE = 60
BLOCK_THRESHOLD = 3
NIGHT_BLOCK_START = 0
NIGHT_BLOCK_END = 7
BLOCKED_PREFIXES = ("/api/", "/ajax/", "/search")
