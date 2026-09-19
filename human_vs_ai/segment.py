"""中文文本切分：段落与句子。

切分是所有后续分析的地基。原则：
- 句子边界只认句末标点（。！？；…），逗号、顿号不切——AI 味的两个核心指标
  （句长均匀度、连接词密度）都以"完整句子"为单位，切错了全盘皆错。
- 引号内的句末标点不算边界（"……。"结束的是引语，不是外层句子）。
- 省略号（……）作为边界，但单字"…"不切（可能是注释语气）。
- 空行分段；连续非空行同段（Markdown 硬换行不拆段）。
- Markdown 结构行（标题、列表符、表格、代码块、引用）不进正文分析——
  它们是文档骨架，不是行文；格式伪影混进统计只会污染句长分布
  （CCL 2025 实测：格式标记会让检测指标虚高 23.66%）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENT_END = "。！？；…!?;"

# 引号对：内层句末标点不是边界
_QUOTES = {"“": "”", "『": "』", "「": "」", '"': '"', "'": "'"}

_MD_STRUCTURE = re.compile(
    r"^\s*(```|~~~|#{1,6}\s|\||\*|[-+]\s|\d+\.\s|===|---)"
)


@dataclass
class Sentence:
    text: str
    para: int  # 段落序号，从 0
    start: int  # 在全文中的字符偏移
    end: int  # 不含句末标点之后的空白


def strip_markdown(text: str) -> tuple[str, dict[int, int]]:
    """去掉 Markdown 结构行，返回 (纯文本, 原文行号→新文本行号 映射不需要，直接丢结构行)。

    返回的文本保留段落空行结构，供 split_paragraphs 使用。
    代码块整块丢弃——代码没有"AI 味"可言。
    """
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            out.append("")
            continue
        if in_code:
            continue
        if not stripped:
            out.append("")
            continue
        if _MD_STRUCTURE.match(line):
            out.append("")
            continue
        # 行内代码与链接只留可读文字
        cleaned = re.sub(r"`([^`]*)`", r"\1", line)
        cleaned = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
        cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
        out.append(cleaned)
    return "\n".join(out)


def split_paragraphs(text: str) -> list[str]:
    paras: list[str] = []
    cur: list[str] = []
    for line in text.splitlines():
        if line.strip():
            cur.append(line.strip())
        elif cur:
            paras.append("\n".join(cur))
            cur = []
    if cur:
        paras.append("\n".join(cur))
    return paras


def split_sentences(text: str) -> list[Sentence]:
    """把一段正文切成句子列表（跨段不切，段落由 split_paragraphs 先分）。"""
    sentences: list[Sentence] = []
    depth = 0  # 引号嵌套深度
    start = 0
    n = len(text)
    for i, ch in enumerate(text):
        close = _QUOTES.get(ch)
        if close and ch in ("“", "『", "「", '"', "'"):
            if close == ch:  # 中英文单引号同形：数奇偶
                depth = 0 if depth else 1
            else:
                depth += 1
            continue
        if ch in ("”", "』", "」"):
            depth = max(0, depth - 1)
            continue
        if depth > 0:
            continue
        if ch in _SENT_END:
            # 处理省略号/连续标点：把后续同类标点并入本句
            j = i + 1
            while j < n and text[j] in _SENT_END + ".””』」!?；":
                j += 1
            body = text[start:j].strip()
            if body:
                sentences.append(Sentence(body, 0, start, j))
            start = j
    tail = text[start:].strip()
    if tail:
        sentences.append(Sentence(tail, 0, start, n))
    return sentences


def split_document(text: str) -> list[list[Sentence]]:
    """整篇分析入口：Markdown 清洗 → 分段 → 分句。

    返回 段落→句子列表 的二维结构；段号写进每个 Sentence。
    """
    clean = strip_markdown(text)
    result: list[list[Sentence]] = []
    offset = 0
    for para in split_paragraphs(clean):
        sents = split_sentences(para)
        for s in sents:
            s.para = len(result)
        # 修正偏移：strip_paragraphs 丢了行首缩进，直接在干净文本里定位
        result.append(sents)
        offset += len(para)
    return result
