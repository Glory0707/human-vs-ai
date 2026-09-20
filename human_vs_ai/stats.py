"""统计特征层：AI 味的量化底座。

全部特征取自可核查的实证研究（出处见 docs/design.md 验证基线）：
- 句长变异系数（burstiness）：人类学术文本 CV≈0.45，AI 改写≈0.38，
  79.3% 的 AI 改写比原文更"平"（TextPulse 2026，6 万篇语料）。
- 词汇丰富度（TTR）：人类"更短但词汇更丰富"（HC3 中文各域一致），
  且是 284 特征研究中唯一跨模型跨域稳健的信号（arXiv 2606.04177）。
- 连接词密度与 4-gram 重复率：部分 slop 模式在 LLM 输出中频率是人类
  的 1000 倍以上（Antislop, arXiv 2510.15061）。

词级统计用 jieba（若已安装）；未安装时回退字级，指标口径不变、精度略降——
不把 jieba 做成硬依赖，`pip install human-vs-ai` 必须零负担。
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

try:
    import jieba  # type: ignore

    if hasattr(jieba, "setLogLevel"):
        # 压掉每次进程启动的 "Building prefix dict..." 四行日志——那是
        # 初始化噪音，会污染每次 CLI 调用的终端输出
        jieba.setLogLevel(logging.ERROR)
    _HAS_JIEBA = True
except ImportError:  # pragma: no cover - 环境相关
    _HAS_JIEBA = False

_PUNCT = re.compile(r"[，。！？；：、…“”‘’《》（）\(\)\[\]【】,\.!\?;:\"'—\-\s]")


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _cv(xs: list[float]) -> float:
    """变异系数 std/mean：衡量"节奏起伏"。样本 <3 句时无意义，返回 NaN。"""
    if len(xs) < 3:
        return math.nan
    m = _mean(xs)
    if m == 0:
        return math.nan
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) / m


def tokenize(text: str) -> list[str]:
    """切词：有 jieba 用 jieba，没有就退化成 2-gram 切片（够算 TTR 的量级）。"""
    if _HAS_JIEBA:
        return [w for w in jieba.lcut(text) if w.strip()]
    clean = _PUNCT.sub("", text)
    if len(clean) < 2:
        return [c for c in clean if c.strip()]
    return [clean[i : i + 2] for i in range(len(clean) - 1)]


@dataclass
class DocStats:
    n_paragraphs: int = 0
    n_sentences: int = 0
    n_chars: int = 0  # 去标点后的正文字符
    sentence_cvs: list[float] = field(default_factory=list)  # 每段内句长 CV
    sentence_cv: float = math.nan  # 全文句长 CV（主指标）
    para_len_cv: float = math.nan  # 段落长度 CV
    ttr: float = math.nan  # MATTR 滑窗词汇丰富度（长度归一）
    conn_density: float = math.nan  # 连接词/句
    ngram_repeat: float = math.nan  # 重复 4-gram 占比
    dash_density: float = math.nan  # 破折号/句
    avg_sentence_len: float = math.nan

    def to_dict(self) -> dict:
        def f(x: float) -> float | None:
            return None if x != x else round(x, 4)  # NaN → null

        return {
            "n_paragraphs": self.n_paragraphs,
            "n_sentences": self.n_sentences,
            "n_chars": self.n_chars,
            "sentence_cv": f(self.sentence_cv),
            "para_len_cv": f(self.para_len_cv),
            "ttr": f(self.ttr),
            "conn_density": f(self.conn_density),
            "ngram_repeat": f(self.ngram_repeat),
            "dash_density": f(self.dash_density),
            "avg_sentence_len": f(self.avg_sentence_len),
            "tokenizer": "jieba" if _HAS_JIEBA else "char-2gram",
        }


def _connective_count(sentences_text: list[str], lexicon: set[str]) -> int:
    n = 0
    for s in sentences_text:
        for w in lexicon:
            n += s.count(w)
    return n


def _four_gram_repeat(text: str) -> float:
    clean = _PUNCT.sub("", text)
    if len(clean) < 8:
        return 0.0
    grams: dict[str, int] = {}
    total = 0
    for i in range(len(clean) - 3):
        g = clean[i : i + 4]
        grams[g] = grams.get(g, 0) + 1
        total += 1
    if not total:
        return 0.0
    repeated = sum(c - 1 for c in grams.values() if c > 1)
    return repeated / total


def mattr(tokens: list[str], window: int = 100) -> float:
    """Moving-Average TTR：每 100 词窗口算一次 TTR 再取均值。

    直接算全文 TTR 会惩罚长文本（越长越容易重复），窗口化之后
    长文短文同一口径可比——这是词汇丰富度研究的标准做法。
    """
    if not tokens:
        return math.nan
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    vals = [
        len(set(tokens[i : i + window])) / window
        for i in range(0, len(tokens) - window + 1)
    ]
    return _mean(vals)


def compute_doc_stats(
    paragraphs: list[list[str]], connective_lexicon: set[str] | None = None
) -> DocStats:
    """从 段落→句子文本列表 计算全文统计。

    connective_lexicon 由规则库传入（与词表规则共用一份连接词表，
    口径统一——报告里的密度数字必须和命中的规则对得上）。
    """
    all_sents: list[str] = [s for para in paragraphs for s in para]
    lens = [len(_PUNCT.sub("", s)) for s in all_sents]
    para_lens = [sum(len(_PUNCT.sub("", s)) for s in para) for para in paragraphs]
    full_text = "".join(all_sents)
    tokens = tokenize(full_text)
    stats = DocStats(
        n_paragraphs=len(paragraphs),
        n_sentences=len(all_sents),
        n_chars=sum(lens),
        sentence_cvs=[_cv([len(_PUNCT.sub("", s)) for s in para]) for para in paragraphs],
        sentence_cv=_cv([float(x) for x in lens]),
        para_len_cv=_cv([float(x) for x in para_lens]),
        ttr=mattr(tokens),
        avg_sentence_len=_mean([float(x) for x in lens]),
    )
    if connective_lexicon:
        stats.conn_density = (
            _connective_count(all_sents, connective_lexicon) / len(all_sents)
            if all_sents
            else math.nan
        )
    stats.ngram_repeat = _four_gram_repeat(full_text)
    # 中文破折号是双字符"——"，按出现次数数，别按字符数数
    n_double = full_text.count("——")
    n_single = full_text.count("—") - 2 * n_double
    stats.dash_density = (
        (n_double + n_single) / len(all_sents) if all_sents else math.nan
    )
    return stats
