"""批量扫描：目录 / glob 通配 → 多文件分析汇总。

单个文件的深度报告归 check；批量回答的是"哪几个文件最重"——
一张按 AI 味指数排序的表，供文档清理排优先级。短于 8 句不出分的
文件照常列出（指数列 —），不参与 --fail-above 判定（宁可不判，不假过）。
"""
from __future__ import annotations

import csv
import glob as _glob
import io as _io
from pathlib import Path

from . import __version__, engine
from .readers import SCAN_EXTS

_DISCLAIMER = "风格提示，不是 AI 判定。"


def resolve_paths(target: str) -> list[Path] | None:
    """check 的文件参数 → 路径列表。None = "-" 走 stdin 单文件。"""
    if target == "-":
        return None
    p = Path(target)
    if p.is_dir():
        return sorted(
            f for f in p.iterdir()
            if f.is_file() and not f.name.startswith(".")
            and f.suffix.lower() in SCAN_EXTS
        )
    if any(ch in target for ch in "*?["):
        return sorted(
            Path(f) for f in _glob.glob(target)
            if Path(f).is_file() and Path(f).suffix.lower() in SCAN_EXTS
        )
    return [p]


def summarize(path: Path, result: engine.AnalysisResult) -> dict:
    s = result.doc_stats
    return {
        "file": str(path),
        "name": path.name,
        "index": round(result.score.index) if result.score else None,
        "high": result.n_high,
        "medium": result.n_medium,
        "low": result.n_low,
        "findings": len(result.findings),
        "n_sentences": s.n_sentences,
        "n_chars": s.n_chars,
    }


def sort_rows(rows: list[dict]) -> list[dict]:
    """指数降序（未出分沉底），同级按发现数、文件名。"""
    return sorted(rows, key=lambda r: (
        r["index"] is None, -(r["index"] or 0), -r["findings"], r["name"]))


def render_terminal(rows: list[dict], profile: str) -> str:
    w_i = max(4, max(len("—" if r["index"] is None else str(r["index"])) for r in rows))
    w_f = max(6, max(len(str(r["findings"])) for r in rows))
    w_s = max(4, max(len(str(r["n_sentences"])) for r in rows))
    lines = [f"human-vs-ai 批量扫描 · {profile} · {len(rows)} 个文件", "─" * 46]
    lines.append(f"{'指数':>{w_i}}  {'发现':>{w_f}}  {'句数':>{w_s}}  文件")
    for r in rows:
        idx = "—" if r["index"] is None else str(r["index"])
        lines.append(
            f"{idx:>{w_i}}  {r['findings']:>{w_f}}  {r['n_sentences']:>{w_s}}  {r['file']}")
    lines.append("─" * 46)
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def render_md(rows: list[dict], profile: str) -> str:
    out = [f"# human-vs-ai 批量扫描（{profile}）", "",
           "| 指数 | 发现 | 高/中/低 | 句数 | 文件 |",
           "|---|---|---|---|---|"]
    for r in rows:
        idx = "—" if r["index"] is None else str(r["index"])
        out.append(f"| {idx} | {r['findings']} | {r['high']}/{r['medium']}/{r['low']} "
                   f"| {r['n_sentences']} | {r['file']} |")
    out.extend(["", _DISCLAIMER])
    return "\n".join(out)


def render_csv(rows: list[dict], profile: str) -> str:
    buf = _io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["file", "index", "findings", "high", "medium", "low",
                     "n_sentences", "n_chars", "profile"])
    for r in rows:
        writer.writerow([r["file"], "" if r["index"] is None else r["index"],
                         r["findings"], r["high"], r["medium"], r["low"],
                         r["n_sentences"], r["n_chars"], profile])
    return buf.getvalue()


def render_json(rows: list[dict], profile: str) -> str:
    import json
    return json.dumps(
        {"tool": "human-vs-ai", "version": __version__, "profile": profile,
         "files": rows, "disclaimer": _DISCLAIMER},
        ensure_ascii=False, indent=2)


def render(rows: list[dict], profile: str, fmt: str) -> str:
    if fmt == "json":
        return render_json(rows, profile)
    if fmt == "csv":
        return render_csv(rows, profile)
    if fmt == "md":
        return render_md(rows, profile)
    if fmt == "terminal":
        return render_terminal(rows, profile)
    raise ValueError(f"未知格式：{fmt}")
