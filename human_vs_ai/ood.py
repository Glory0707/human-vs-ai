"""域外文体探测：文言/诗行文本超出评测语料域，指数会系统性虚高。

动机（v0.17.0 实测）：C-ReD 作文域真人最高分是一篇仿古长诗（98 分），
高考文言满分作文同样高分——评分系数拟合在现代白话上，文言与诗行
没有"AI 味"可言却天然命中统计特征（句长整齐、TTR 高）。检测到域外
文体时报告显式提示，指数仅供参考，而不是让虚高分数挂着不解释。

判据（在 C-ReD 全量 10.4 万篇上校准：正样本 5/5 命中，误报 0.005%）：
- classical（文言/半文言）：低"的地得"密度 × 高文言虚词密度 × 零"了"字。
  虚词表只收白话零频字——"或/亦/耳/耶"会坑了"或/亦/耳机/耶鲁"（实测教训），
  "了"是白话体最顽固的残留，文言几乎为零，是最有效的第三佐证。
- verse（等长对句诗行）：高占比"恰好两分句、各 5-9 字"的句子，且对句
  长度只有一种（五言或七言）。单一长度排除四字成语排比（白话快节奏
  影评实测误报），5-9 字窗排除长短错落的散文。
"""
from __future__ import annotations

import re
from collections import Counter

from .stats import PUNCT  # 标点口径单一事实源：与统计层逐字一致靠共用，不靠人肉同步

# 文言虚词强表：白话零频字。禁收"之/者/也/或/亦/耳/耶"——白话/专名误伤实测教训
_STRONG = "乎哉兮矣焉欤俟汝尓乃遂皆曰"
_DE = "的地得"
_LE = "了"
# 对句内部分隔；预编译——detect 对每句调用，长文上缓存查找开销可观
_INNER_RE = re.compile("[，、；]")
_N_MIN = 80        # 更短的正文字数信号不稳，不判
_DE_MAX = 0.010    # classical：的地得密度上限
_STRONG_MIN = 0.008  # classical：文言虚词密度下限
_LE_MAX = 0.006    # classical：了字密度上限
_BAL_MIN = 0.60    # verse：等长对句占比下限
_BAL_SENTS_MIN = 4  # verse：最少句数
_PART_LEN = (5, 9)  # verse：对句分句字数窗（五言~九言）


def detect(sentences: list[str]) -> list[str]:
    """从句子文本列表判定域外文体，返回 kinds（"classical"/"verse" 子集）。

    入参用与统计层同一口径的展平句子（blocks → sents），两端（Py/JS）
    都从切分结果取数，不各自重切。
    """
    clean = PUNCT.sub("", "".join(sentences))
    n = len(clean)
    if n < _N_MIN:
        return []
    cnt = Counter(clean)
    de = sum(cnt[c] for c in _DE) / n
    strong = sum(cnt[c] for c in _STRONG) / n
    le = cnt[_LE] / n

    kinds: list[str] = []
    if de < _DE_MAX and strong >= _STRONG_MIN and le < _LE_MAX:
        kinds.append("classical")

    bal = 0
    lens: set[int] = set()
    lo, hi = _PART_LEN
    for s in sentences:
        parts = [p for p in _INNER_RE.split(s) if p.strip()]
        ls = [len(PUNCT.sub("", p)) for p in parts]
        if len(parts) == 2 and all(lo <= x <= hi for x in ls):
            bal += 1
            lens.update(ls)
    if bal >= _BAL_SENTS_MIN and bal / len(sentences) >= _BAL_MIN and len(lens) == 1:
        kinds.append("verse")
    return kinds
