"""报告渲染：终端（ANSI 彩色）/ Markdown / JSON 三种出口，同一份内容。

句子级发现按"句"聚合——同一句命中三条规则就讲一次这句话、列三条
问题，不重复贴三遍原句（界面减法：同一信息只出现一次）。
doc 级发现独立成条。

免责不是客套——它是产品定位本身（见 design.md 设计原则 1），
每次输出都带着走，防止报告被截图传播时丢掉语境变成"AI 判决书"。
"""
from __future__ import annotations

import json
import sys
from collections import OrderedDict

from .engine import AnalysisResult, Finding
from . import __version__

_TIER_LABEL = {"lexical": "词表", "syntactic": "句式", "structural": "结构", "statistical": "统计"}
_SEV_LABEL = {"high": "高", "medium": "中", "low": "低", "hint": "弱"}
_SEV_RANK = {"high": 0, "medium": 1, "low": 2, "hint": 3}

_DISCLAIMER = "风格提示，不是 AI 判定；单条命中不构成证据。"

# 弱命中列表的展示上限：hundreds-of-hints 的长文里它只是参考信息，
# 全量列出会淹没正文发现（JSON 出口不带截断——事实源永远完整）
_HINTS_MAX = 12


def _fmt(value: float) -> str:
    return "—" if value != value else f"{value:.2f}"


def stats_lines(result: AnalysisResult) -> list[str]:
    s = result.doc_stats
    return [
        f"规模：{s.n_paragraphs} 段 · {s.n_sentences} 句 · {s.n_chars} 字",
        f"节奏：句长 CV {_fmt(s.sentence_cv)}（人类基线 ≈0.45，越低越平）"
        f" · 段长 CV {_fmt(s.para_len_cv)}",
        f"词汇：TTR {_fmt(s.ttr)} · 连接词密度 {_fmt(s.conn_density)}"
        f"{' 条/句' if s.conn_density == s.conn_density else ''}"
        f" · 4-gram 重复率 {_fmt(s.ngram_repeat)}",
    ]


def _group_by_sentence(findings: list[Finding]):
    """(段号, 原句) 相同的句子级发现聚成一组，保持报告顺序；doc 级单独返回。"""
    groups: OrderedDict = OrderedDict()
    doc_level: list[Finding] = []
    for f in findings:
        if f.para < 0:
            doc_level.append(f)
        else:
            groups.setdefault((f.para, f.sentence), []).append(f)
    return list(groups.values()), doc_level


def _taste_suffix(group: list[Finding]) -> str:
    """口味条目编号（personal profile 专有）——指向 docs/taste_zhouao.md。"""
    tags = [t for f in group for t in ([f.taste] if f.taste else [])]
    tags = list(dict.fromkeys(tags))
    return f" · {'/'.join(tags)}" if tags else ""


def _group_title(group: list[Finding]) -> str:
    top = min((f.severity for f in group), key=lambda s: _SEV_RANK[s])
    ids = " + ".join(f.rule_id for f in group)
    names = " + ".join(f.rule_name for f in group)
    loc = f"¶{group[0].para + 1}"
    return f"[{_SEV_LABEL[top]}] {ids} {names}{_taste_suffix(group)} · {loc}"


def render_terminal(result: AnalysisResult) -> str:
    use_color = sys.stdout.isatty()
    C = (
        lambda code, s: f"\033[{code}m{s}\033[0m"
        if use_color
        else s
    )
    sev_color = {"high": "1;31", "medium": "33", "low": "36", "hint": "90"}

    out: list[str] = []
    out.append(C("1", f"human-vs-ai v{__version__} · {result.profile} profile"))
    out.append("─" * 46)
    out.extend(stats_lines(result))
    out.append("")
    if not result.findings:
        out.append(C("32", "未发现明显的模板化写作模式。"))
    else:
        explained: set[str] = set()
        groups, doc_level = _group_by_sentence(result.findings)
        n_hi, n_md, n_lo = result.n_high, result.n_medium, result.n_low
        out.append(C("1", f"发现 {len(result.findings)} 处（高 {n_hi} · 中 {n_md} · 低 {n_lo}）"))
        out.append("")
        for group in groups:
            title = _group_title(group)
            top = min((f.severity for f in group), key=lambda s: _SEV_RANK[s])
            out.append(C(sev_color[top], title))
            sent = group[0].sentence
            show = sent if len(sent) <= 60 else sent[:57] + "…"
            out.append(f"  「{show}」")
            matches = [m for f in group for m in f.matches]
            out.append(C("90", f"  命中：{'、'.join(dict.fromkeys(matches))}"))
            for f in group:
                # 同一规则的解释全文只讲一次——第 6 次"首先"不需要重读同一段话
                if f.rule_id in explained:
                    continue
                explained.add(f.rule_id)
                out.append(f"  · {f.explanation}")
                if f.suggestion:
                    out.append(C("32", f"    → {f.suggestion}"))
            out.append("")
        for f in doc_level:
            tag = f" · {f.taste}" if f.taste else ""
            out.append(C(sev_color[f.severity], f"[{_SEV_LABEL[f.severity]}] {f.rule_id} {f.rule_name}{tag} · 全文"))
            out.append(C("90", f"  命中：{f.matches[0]}"))
            out.append(f"  {f.explanation}")
            if f.suggestion:
                out.append(C("32", f"  → {f.suggestion}"))
            out.append("")
    if result.hints:
        shown = result.hints[:_HINTS_MAX]
        out.append(C("90", f"另有 {len(result.hints)} 处孤立弱命中，仅供参考"
                          + (f"（列前 {len(shown)} 处）" if len(shown) < len(result.hints) else "") + "："))
        for f in shown:
            out.append(C("90", f"  · {f.rule_id} {f.rule_name} ¶{f.para + 1}"))
        out.append("")
    out.append(C("90", "─" * 46))
    out.append(C("90", _DISCLAIMER))
    return "\n".join(out)


def render_markdown(result: AnalysisResult) -> str:
    out: list[str] = []
    out.append(f"# human-vs-ai 分析报告（{result.profile}）")
    out.append("")
    out.append("## 全文统计")
    out.append("")
    for line in stats_lines(result):
        out.append(f"- {line}")
    out.append("")
    out.append(f"## 发现（{len(result.findings)} 处）")
    out.append("")
    if not result.findings:
        out.append("未发现明显的模板化写作模式。")
    explained: set[str] = set()
    groups, doc_level = _group_by_sentence(result.findings)
    for group in groups:
        out.append(f"### {_group_title(group)}")
        out.append("")
        out.append(f"> {group[0].sentence}")
        out.append("")
        matches = [m for f in group for m in f.matches]
        out.append(f"**命中**：{'、'.join(dict.fromkeys(matches))}")
        out.append("")
        for f in group:
            if f.rule_id in explained:
                continue  # 同一规则的解释全文只讲一次（与 terminal 口径一致）
            explained.add(f.rule_id)
            out.append(f"**{f.rule_id}** {f.explanation}")
            if f.suggestion:
                out.append("")
                out.append(f"**建议**：{f.suggestion}")
            out.append("")
    for f in doc_level:
        tag = f" · {f.taste}" if f.taste else ""
        out.append(f"### [{_SEV_LABEL[f.severity]}] {f.rule_id} {f.rule_name}{tag}（全文）")
        out.append("")
        out.append(f"**命中**：{f.matches[0]}")
        out.append("")
        out.append(f.explanation)
        if f.suggestion:
            out.append("")
            out.append(f"**建议**：{f.suggestion}")
        out.append("")
    if result.hints:
        out.append("## 孤立弱命中（仅供参考）")
        out.append("")
        shown = result.hints[:_HINTS_MAX]
        if len(shown) < len(result.hints):
            out.append(f"共 {len(result.hints)} 处，列前 {len(shown)} 处：")
            out.append("")
        for f in shown:
            out.append(f"- {f.rule_id} {f.rule_name}（¶{f.para + 1}）")
        out.append("")
    out.append("---")
    out.append("")
    out.append(_DISCLAIMER)
    return "\n".join(out)


def render_json(result: AnalysisResult) -> str:
    return json.dumps(
        {
            "tool": "human-vs-ai",
            "version": __version__,
            "profile": result.profile,
            "stats": result.doc_stats.to_dict(),
            "findings": [f.to_dict() for f in result.findings],
            "hints": [f.to_dict() for f in result.hints],
            "disclaimer": _DISCLAIMER,
        },
        ensure_ascii=False,
        indent=2,
    )


def render(result: AnalysisResult, fmt: str) -> str:
    if fmt == "json":
        return render_json(result)
    if fmt == "md":
        return render_markdown(result)
    if fmt == "terminal":
        return render_terminal(result)
    raise ValueError(f"未知格式：{fmt}")
