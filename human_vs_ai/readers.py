"""文本读取：txt/md 直接解码；docx/odt 走 zip+XML 提取——零新增依赖。

docx/odt 本质是 zip 包 XML：docx 取 word/document.xml 的 w:p（表格单元格
里的段落也在内），odt 取 content.xml 的 text:p / text:h。段落间以空行
连接，与引擎"空行分段"口径对齐；段内换行/制表照搬。只取文字运行
（w:t），修订删除（w:delText）与域代码（w:instrText）不进分析。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SCAN_EXTS = (".md", ".markdown", ".txt", ".docx", ".odt")


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.is_dir():
        raise IsADirectoryError(str(p))
    if p.suffix.lower() in (".docx", ".odt"):
        return _from_zip(p)
    try:
        return p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return p.read_text(encoding="gb18030", errors="replace")


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _from_zip(path: Path) -> str:
    suffix = path.suffix.lower()
    member = "word/document.xml" if suffix == ".docx" else "content.xml"
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read(member)
    except (zipfile.BadZipFile, KeyError):
        raise ValueError(f"不是有效的 {suffix.lstrip('.')} 文件：{path.name}") from None
    root = ET.fromstring(xml)
    if suffix == ".docx":
        return _docx_paras(root)
    return _odt_paras(root)


def _docx_paras(root: ET.Element) -> str:
    """document.xml 里全部 w:p 按文档顺序取文字；嵌套段落（文本框）
    借 seen 集合防文字重复计。"""
    out: list[str] = []
    seen: set[int] = set()
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        parts: list[str] = []
        for node in p.iter():
            if id(node) in seen:
                continue
            seen.add(id(node))
            tag = _local(node.tag)
            if tag == "t":
                parts.append(node.text or "")
            elif tag == "br":
                parts.append("\n")
            elif tag == "tab":
                parts.append("\t")
        text = "".join(parts).strip()
        if text:
            out.append(text)
    return "\n\n".join(out)


_ODT_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _odt_text(node: ET.Element) -> str:
    parts = [node.text or ""]
    for child in node:
        tag = _local(child.tag)
        if tag == "line-break":
            parts.append("\n")
        elif tag == "tab":
            parts.append("\t")
        elif tag == "s":
            count = child.get(f"{{{_ODT_TEXT_NS}}}c", "1")
            parts.append(" " * max(1, int(count) if count.isdigit() else 1))
        else:
            parts.append(_odt_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odt_paras(root: ET.Element) -> str:
    out = []
    for p in root.iter():
        if _local(p.tag) not in ("p", "h"):
            continue
        text = _odt_text(p).strip()
        if text:
            out.append(text)
    return "\n\n".join(out)
