"""域外探测：文本超出评分语料域时，指数会系统性失真。

两族判据，性质不同所以分两个函数：

**文体域外**（detect）：文言/诗行没有"AI 味"可言却天然命中统计特征
（句长整齐、TTR 高），指数会系统性虚高。动机（v0.17.0 实测）：C-ReD
作文域真人最高分是一篇仿古长诗（98 分），高考文言满分作文同样高分。

判据（在 C-ReD 全量 10.4 万篇上校准：正样本 5/5 命中，误报 0.005%）：
- classical（文言/半文言）：低"的地得"密度 × 高文言虚词密度 × 零"了"字。
  虚词表只收白话零频字——"或/亦/耳/耶"会坑了"或/亦/耳机/耶鲁"（实测教训），
  "了"是白话体最顽固的残留，文言几乎为零，是最有效的第三佐证。
- verse（等长对句诗行）：高占比"恰好两分句、各 5-9 字"的句子，且对句
  长度只有一种（五言或七言）。单一长度排除四字成语排比（白话快节奏
  影评实测误报），5-9 字窗排除长短错落的散文。

**文种域外**（detect_genre，只对声明了 genre_ood 的 profile 生效）：
official 评分系数绑定**事务文种**（通知/通报/意见/方案正文），省级门户的
"印发类"（通知 + 规划/方案全文附录）与"批复"超出校准文种域。动机
（v0.18.1 泛化体检）：湖北/四川真人公文里印发类 p50=76、批复 p90=98，
同文种对照 AUROC 印发 0.294（反转）、批复 0.614——系数在这个文种上
不携带作者信息。见 _qa/generalization-check.md 与 docs/rules.md §8。
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
_N_MIN = 80        # 更短的正文字数信号不稳，不判（两族判据共用：短片段不判文种）
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


# 文种判据：只用公文正体的结构短语，不用内容词——文种是文档属性，
# 与"像不像 AI"无关，所以判据必须与分数、与作者无关。
# 标定（标注来自标题/生成 prompt，与判据独立；语料同 v0.18.1 泛化体检）：
#   "印发给你们，请"：真人印发 18/18、AI 印发 14/14；真人批复 0/20、
#     真人事务 0/8、AI 普通公文 2/27（核为真·印发文）；拟合集 16/87
#   "批复如下"：真人批复 20/20、AI 批复 11/11；其余各组 0（含拟合集 87 篇）
_RE_YINFA = re.compile(r"印发给你们，请")
_RE_PIFU = re.compile(r"批复如下")


def detect_genre(sentences: list[str]) -> list[str]:
    """判定公文文种是否超出评分系数的校准域，返回 kinds。

    与 detect 分开的原因：文体判据（文言/诗行）判的是"文本形状"，各
    profile 通用；文种判据判的是"这篇属于哪类公文"，只对 official 的
    系数有意义——由调用方按 profile 决定是否调用（engine 看 scoring.genre_ood）。

    入参与 detect 同口径：统计层展平后的句子文本，两端都从这里取数。
    """
    text = "".join(sentences)
    if len(PUNCT.sub("", text)) < _N_MIN:
        return []
    kinds: list[str] = []
    if _RE_YINFA.search(text):
        kinds.append("issuance-notice")
    if _RE_PIFU.search(text):
        kinds.append("approval-reply")
    return kinds
