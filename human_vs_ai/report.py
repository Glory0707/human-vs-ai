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

from .engine import AnalysisResult, Score, Finding
from . import __version__

_SEV_LABEL = {"high": "高", "medium": "中", "low": "低", "hint": "弱"}
_SEV_RANK = {"high": 0, "medium": 1, "low": 2, "hint": 3}

_DISCLAIMER = "风格提示，不是 AI 判定。"

# 弱命中列表的展示上限：hundreds-of-hints 的长文里它只是参考信息，
# 全量列出会淹没正文发现（JSON 出口不带截断——事实源永远完整）
_HINTS_MAX = 12

# 评分特征 → 报告用短标签（components 键序即 scoring YAML 特征序）
_SCORE_LABEL = {
    "hit_density": "规则",
    "sentence_cv": "节奏",
    "ttr": "词汇",
    "ngram_repeat": "重复",
    "conn_density": "连接词",
}

# 域外 kinds → 报告用名（引擎 ood 的 kinds）。按"文体/文种"两族分行给提示：
# 文言/诗行是"形状"超出语料域，印发/批复是"文种"超出系数校准域（系数按
# 事务公文拟合）——合成一行说不清为什么仅供参考
_OOD_NAME = {
    "classical": "文言",
    "verse": "等长对句诗行",
    "issuance-notice": "印发类",
    "approval-reply": "批复类",
}
_OOD_KIND = {"classical": "文体", "verse": "文体",
             "issuance-notice": "文种", "approval-reply": "文种"}
_OOD_WHY = {"文体": "指数仅供参考", "文种": "系数按事务公文校准，指数仅供参考"}


def _band_text(score: Score) -> str:
    """分数读数：相对校准语料真人分布的位置，比裸 p50/p90 数字可读。"""
    if score.index > score.human_p90:
        return "超过 90% 校准真人"
    if score.index > score.human_p50:
        return "超过半数校准真人"
    return "低于半数校准真人"


def _ood_lines(result: AnalysisResult) -> list[str]:
    groups: dict[str, list[str]] = {}
    for kind in result.ood:
        groups.setdefault(_OOD_KIND.get(kind, "文体"), []).append(_OOD_NAME.get(kind, kind))
    return [f"※ {k}域外（{'、'.join(groups[k])}）：{_OOD_WHY[k]}"
            for k in ("文体", "文种") if k in groups]


def _heat_line(result: AnalysisResult) -> str:
    if not result.para_heat:
        return ""
    shown = result.para_heat[:3]
    parts = " · ".join(f"¶{h['para'] + 1} {h['density']:.2f}" for h in shown)
    return f"段落热度：{parts}"


def _score_line(score: Score) -> str:
    # 整数显示：逻辑回归压到 0-100 后小数位是假精度（网页端同口径）
    return f"AI 味指数：{round(score.index)} / 100（{_band_text(score)}）"


def _score_components(score: Score) -> str:
    parts = []
    for feat, v in score.components.items():
        label = _SCORE_LABEL.get(feat, feat)
        parts.append(f"{label} {v:+.0f}")
    return "构成：" + " · ".join(parts) if parts else ""


def _fmt(value: float) -> str:
    return "—" if value != value else f"{value:.2f}"


def stats_lines(result: AnalysisResult) -> list[str]:
    s = result.doc_stats
    rows = []
    # 综合分是报告的第一行——它是用户要的"整体判断"，逐句发现在后
    if result.score:
        rows.append(_score_line(result.score))
        rows.append(_score_components(result.score))
    elif result.scoring_note:
        # 够 8 句却没分：给一行原因，免得用户在各文体间切换时纳闷分去哪了
        rows.append(f"AI 味指数：—（{result.scoring_note}）")
    rows.extend(_ood_lines(result))
    rows.append(_heat_line(result))
    rows.append(f"规模：{s.n_paragraphs} 段 · {s.n_sentences} 句 · {s.n_chars} 字")
    # 统计三行只在样本够判定时展示（口径与 doc 规则的 min_sentences 一致）：
    # 一两句话的文本里 CV 全是"—"、TTR 恒为 1，展示出来全是噪音
    if s.n_sentences < 8:
        return rows
    rows.append(f"节奏：句长 CV {_fmt(s.sentence_cv)} · 段长 CV {_fmt(s.para_len_cv)}")
    rows.append(f"词汇：TTR {_fmt(s.ttr)} · 连接词密度 {_fmt(s.conn_density)}"
                f"{' 条/句' if s.conn_density == s.conn_density else ''}"
                f" · 4-gram 重复率 {_fmt(s.ngram_repeat)}")
    return rows


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
    tags = list(dict.fromkeys(f.taste for f in group if f.taste))
    return f" · {'/'.join(tags)}" if tags else ""


def _group_top(group: list[Finding]) -> str:
    return min((f.severity for f in group), key=lambda s: _SEV_RANK[s])


def _group_title(group: list[Finding]) -> str:
    # 重复句折叠后同一规则会出现几十次——标题去重（matches 同口径）
    ids = " + ".join(dict.fromkeys(f.rule_id for f in group))
    names = " + ".join(dict.fromkeys(f.rule_name for f in group))
    loc = f"¶{group[0].para + 1}"
    return f"[{_SEV_LABEL[_group_top(group)]}] {ids} {names}{_taste_suffix(group)} · {loc}"


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
        out.append(C("32", "未发现模板化写作"))
    else:
        explained: set[str] = set()
        groups, doc_level = _group_by_sentence(result.findings)
        n_hi, n_md, n_lo = result.n_high, result.n_medium, result.n_low
        out.append(C("1", f"发现 {len(result.findings)} 处（高 {n_hi} · 中 {n_md} · 低 {n_lo}）"))
        out.append("")
        for group in groups:
            title = _group_title(group)
            top = _group_top(group)
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
        cap = f"（列前 {len(shown)} 处）" if len(shown) < len(result.hints) else ""
        out.append(C("90", f"另有 {len(result.hints)} 处弱命中{cap}："))
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
        out.append("未发现模板化写作")
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
        out.append("## 弱命中")
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
            "score": result.score.to_dict() if result.score else None,
            "score_note": result.scoring_note or None,
            "ood": result.ood or None,
            "para_heat": result.para_heat or None,
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
