"""中文文本切分：块（段落/列表/表格）与句子。

切分是所有后续分析的地基。原则：
- 句子边界只认句末标点（。！？；…），逗号、顿号不切——AI 味的两个核心指标
  （句长均匀度、连接词密度）都以"完整句子"为单位，切错了全盘皆错。
- 引号内的句末标点不算边界（"……。"结束的是引语，不是外层句子）。
- 省略号（……）作为边界，但单字"…"不切（可能是注释语气）。
- Markdown 处理（2026-09 修订）：标题行、代码块整块丢弃；列表项与表格行的
  **内容保留**，各自成句——问答/自媒体正文大量用列表写，整段丢弃会让核心
  句式完全漏检。列表/表格块不触发"独句总结段"（bullet 短是格式不是盖章）。
  列表项没有句末标点时也独立成句，不与相邻项拼接。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SENT_END = "。！？；…!?;"

# 引号对：内层句末标点不是边界
_OPEN_QUOTES = "“『「"
_CLOSE_QUOTES = "”』」"

# ---------- Markdown 行级识别 ----------

_RE_FENCE = re.compile(r"^\s*(?:```|~~~)")
_RE_HEADING = re.compile(r"^\s*#{1,6}(?:\s|$)")
_RE_HR = re.compile(r"^\s*(?:-\s*){2,}-?\s*$|^\s*(?:\*\s*){2,}\*?\s*$|^\s*_{3,}\s*$")
_RE_SETEXT = re.compile(r"^\s*=+\s*$")
# 列表符：- * + 后须跟空格；数字编号允许"1. 首"（带空格）与"1.首"（无空格），
# 但用前瞻挡掉"3.14"这类小数点
_RE_LIST = re.compile(r"^\s*(?:[-*+]\s+|\d+[.、)](?=\s|\D))\s*(.*)$")
_RE_CHECKBOX = re.compile(r"^\s*\[[ xX]\]\s*")
_RE_QUOTE_PREFIX = re.compile(r"^\s*>+\s?")
_RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")

# ---------- 行内清洗 ----------

_RE_CODE = re.compile(r"`([^`]*)`")
_RE_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_RE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_URL = re.compile(r"(?:https?://|www\.)[^\s，。；！？、）)】」』]+", re.I)
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 只清内容含中文的星号强调（*斜体*/**粗体**），避免误伤 3*5 这类算式
_RE_EMPH = re.compile(r"\*{1,3}(?!\s)([^*]*?[一-鿿][^*]*?)(?<!\s)\*{1,3}")


@dataclass
class Sentence:
    text: str
    para: int  # 块序号，从 0


@dataclass
class Block:
    kind: str = "para"  # para（普通段）/ list（列表）/ table（表格）
    sents: list[Sentence] = field(default_factory=list)


def _inline_clean(line: str) -> str:
    """剥掉行内 Markdown 标记与裸链接，只留可读文字。"""
    line = _RE_CODE.sub(r"\1", line)
    line = _RE_IMG.sub(r"\1", line)
    line = _RE_LINK.sub(r"\1", line)
    line = _RE_URL.sub("", line)
    line = _RE_EMAIL.sub("", line)
    line = _RE_EMPH.sub(r"\1", line)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.strip()


def _line_units(text: str) -> list[tuple[str, str]]:
    """原始行 → 有序单元：("p"|"li"|"tr", 文本) 或 ("b", "") 边界。

    标题/分隔线/代码块不产文本，只产边界。
    """
    units: list[tuple[str, str]] = []
    in_code = False
    for raw in text.splitlines():
        s = _RE_QUOTE_PREFIX.sub("", raw.strip())
        if _RE_FENCE.match(s):
            in_code = not in_code
            units.append(("b", ""))
            continue
        if in_code or not s:
            units.append(("b", ""))
            continue
        if _RE_HEADING.match(s) or _RE_HR.match(s) or _RE_SETEXT.match(s):
            units.append(("b", ""))
            continue
        m = _RE_LIST.match(s)
        if m:
            item = _RE_CHECKBOX.sub("", m.group(1)).strip()
            if item:
                units.append(("li", _inline_clean(item)))
            else:
                units.append(("b", ""))
            continue
        if "|" in s:
            if _RE_TABLE_SEP.match(s):
                units.append(("b", ""))
                continue
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            row = _inline_clean("，".join(c for c in cells if c))
            units.append(("tr", row) if row else ("b", ""))
            continue
        units.append(("p", _inline_clean(s)))
    return units


def _text_blocks(text: str) -> list[tuple[str, list[str]]]:
    """_line_units 的结果组装成块序列 (kind, [文本, ...])。

    strip_markdown 与 split_document 的共同底座——两个出口必须吃同一份
    解析结果，口径才不会漂移（JS 端同构函数名 groupBlocks）。
    """
    blocks: list[tuple[str, list[str]]] = []
    p_buf: list[str] = []
    run: list | None = None

    def flush_prose() -> None:
        nonlocal p_buf
        if p_buf:
            blocks.append(("para", ["\n".join(p_buf)]))
            p_buf = []

    def flush_run() -> None:
        nonlocal run
        if run:
            blocks.append((run[0], run[1]))
            run = None

    for kind, t in _line_units(text):
        if kind == "b":
            flush_prose()
            flush_run()
        elif kind in ("li", "tr"):
            flush_prose()
            block_kind = "list" if kind == "li" else "table"
            if run is None or run[0] != block_kind:
                flush_run()
                run = [block_kind, []]
            run[1].append(t)
        else:
            flush_run()
            p_buf.append(t)
    flush_prose()
    flush_run()
    return blocks


def strip_markdown(text: str) -> str:
    """去掉 Markdown 标记，返回纯文本（保留段落空行结构）。

    与 split_document 同一解析结果渲染，保证两个出口口径一致。
    代码块整块丢弃——代码没有"AI 味"可言。
    """
    return "\n\n".join(
        units[0] if kind == "para" else "\n".join(units)
        for kind, units in _text_blocks(text)
    )


def split_sentences(text: str) -> list[Sentence]:
    """把一段正文切成句子列表（跨段不切，段落由上层先分）。

    引号规则：中文引号（“「『）按嵌套计数；ASCII 双引号按奇偶切换；
    ASCII 单引号不参与——英文所有格/缩写（it's）远比引语常见，
    拿它当引号切分会吞掉后续句末标点（首轮审计实证）。
    """
    sentences: list[Sentence] = []
    depth = 0  # 引号嵌套深度
    start = 0
    n = len(text)
    for i, ch in enumerate(text):
        if ch in _OPEN_QUOTES:
            depth += 1
            continue
        if ch in _CLOSE_QUOTES:
            depth = max(0, depth - 1)
            continue
        if ch == chr(34):  # ASCII 双引号(chr 写法避免引号字符被编辑器/生成环节偷换成全角)
            depth = 0 if depth else 1
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
                sentences.append(Sentence(body, 0))
            start = j
    tail = text[start:].strip()
    if tail:
        sentences.append(Sentence(tail, 0))
    return sentences


def split_document(text: str) -> list[Block]:
    """整篇分析入口：Markdown 解析 → 块（段落/列表/表格）→ 分句。

    普通段按空行分段、段内硬换行不拆；列表/表格的每个条目独立分句
    （条目没有句末标点也算一句，不与相邻条目拼接）。
    """
    blocks: list[Block] = []
    for kind, units in _text_blocks(text):
        block = Block(kind=kind)
        if kind == "para":
            block.sents = split_sentences(units[0])
        else:
            for unit in units:
                block.sents.extend(split_sentences(unit))
        blocks.append(block)
    for pi, block in enumerate(blocks):
        for s in block.sents:
            s.para = pi
    return blocks
