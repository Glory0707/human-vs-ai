"""报告渲染：终端（ANSI 彩色）/ Markdown / JSON 三种出口，同一份内容。

句子级发现按"句"聚合——同一句命中三条规则就讲一次这句话、列三条
问题，不重复贴三遍原句（界面减法：同一信息只出现一次）。
doc 级发现独立成条。

"""
from __future__ import annotations

import json
import sys
import time
from collections import OrderedDict

from .engine import AnalysisResult, Score, Finding
from . import __version__

# 严重级/特征/免责等报告用词：terminal/md/json 与 htreport 共用，改名须同步
SEV_LABEL = {"high": "高", "medium": "中", "low": "低", "hint": "弱"}
_SEV_RANK = {"high": 0, "medium": 1, "low": 2, "hint": 3}

DISCLAIMER = "风格提示，不是 AI 判定。"

# 弱命中列表的展示上限：hundreds-of-hints 的长文里它只是参考信息，
# 全量列出会淹没正文发现（JSON 出口不带截断——事实源永远完整）
HINTS_MAX = 12

# 评分特征 → 报告用短标签（components 键序即 scoring YAML 特征序）
SCORE_LABEL = {
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
    "non-chinese": "非中文文本",
}
_OOD_KIND = {"classical": "文体", "verse": "文体",
             "issuance-notice": "文种", "approval-reply": "文种"}
_OOD_WHY = {"文体": "指数仅供参考", "文种": "本篇仅供参考"}


def _ood_lines(result: AnalysisResult) -> list[str]:
    groups: dict[str, list[str]] = {}
    for kind in result.ood:
        if kind == "officialese":  # 切场景提示，专属行输出，不走域外分组
            continue
        groups.setdefault(_OOD_KIND.get(kind, "文体"), []).append(_OOD_NAME.get(kind, kind))
    lines = [f"※ {k}域外（{'、'.join(groups[k])}）：{_OOD_WHY[k]}"
             for k in ("文体", "文种") if k in groups]
    if "officialese" in result.ood:
        lines.append("※ 公文/公务文书风格：official 场景更准")
    return lines


def _score_line(score: Score) -> str:
    # 整数显示：逻辑回归压到 0-100 后小数位是假精度（网页端同口径）
    return f"AI 味指数：{round(score.index)} / 100"


def _score_components(score: Score) -> str:
    parts = []
    for feat, v in score.components.items():
        label = SCORE_LABEL.get(feat, feat)
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
    rows.append(f"{s.n_paragraphs} 段 · {s.n_sentences} 句 · {s.n_chars} 字")
    # 统计三行只在样本够判定时展示（口径与 doc 规则的 min_sentences 一致）：
    # 一两句话的文本里 CV 全是"—"、TTR 恒为 1，展示出来全是噪音
    if s.n_sentences < 8:
        return rows
    rows.append(f"句长 CV {_fmt(s.sentence_cv)} · 段长 CV {_fmt(s.para_len_cv)}")
    rows.append(f"TTR {_fmt(s.ttr)} · 连接词 {_fmt(s.conn_density)}"
                f"{'/句' if s.conn_density == s.conn_density else ''}"
                f" · 重复率 {_fmt(s.ngram_repeat)}")
    return rows


def group_by_sentence(findings: list[Finding]):
    """(段号, 原句) 相同的句子级发现聚成一组，保持报告顺序；doc 级单独返回。"""
    groups: OrderedDict = OrderedDict()
    doc_level: list[Finding] = []
    for f in findings:
        if f.para < 0:
            doc_level.append(f)
        else:
            groups.setdefault((f.para, f.sentence), []).append(f)
    return list(groups.values()), doc_level


def group_top(group: list[Finding]) -> str:
    return min((f.severity for f in group), key=lambda s: _SEV_RANK[s])


def _group_title(group: list[Finding]) -> str:
    # 重复句折叠后同一规则会出现几十次——标题去重（matches 同口径）
    ids = " + ".join(dict.fromkeys(f.rule_id for f in group))
    names = " + ".join(dict.fromkeys(f.rule_name for f in group))
    loc = f"¶{group[0].para + 1}"
    return f"[{SEV_LABEL[group_top(group)]}] {ids} {names} · {loc}"


def render_terminal(result: AnalysisResult) -> str:
    use_color = sys.stdout.isatty()
    C = (
        lambda code, s: f"\033[{code}m{s}\033[0m"
        if use_color
        else s
    )
    sev_color = {"high": "1;31", "medium": "33", "low": "36", "hint": "90"}

    out: list[str] = []
    out.append(C("1", f"human-vs-ai v{__version__} · {result.profile}"))
    out.append("─" * 46)
    out.extend(stats_lines(result))
    out.append("")
    if not result.findings:
        out.append(C("32", "未发现模板化写作"))
    else:
        explained: set[str] = set()
        groups, doc_level = group_by_sentence(result.findings)
        n_hi, n_md, n_lo = result.n_high, result.n_medium, result.n_low
        out.append(C("1", f"发现 {len(result.findings)} 处（高 {n_hi} · 中 {n_md} · 低 {n_lo}）"))
        out.append("")
        for group in groups:
            title = _group_title(group)
            top = group_top(group)
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
            out.append(C(sev_color[f.severity], f"[{SEV_LABEL[f.severity]}] {f.rule_id} {f.rule_name} · 全文"))
            out.append(C("90", f"  命中：{f.matches[0]}"))
            out.append(f"  {f.explanation}")
            if f.suggestion:
                out.append(C("32", f"  → {f.suggestion}"))
            out.append("")
    if result.hints:
        shown = result.hints[:HINTS_MAX]
        cap = f"（列前 {len(shown)} 处）" if len(shown) < len(result.hints) else ""
        out.append(C("90", f"另有 {len(result.hints)} 处弱命中{cap}："))
        for f in shown:
            out.append(C("90", f"  · {f.rule_id} {f.rule_name} ¶{f.para + 1}"))
        out.append("")
    out.append(C("90", "─" * 46))
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
    groups, doc_level = group_by_sentence(result.findings)
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
        out.append(f"### [{SEV_LABEL[f.severity]}] {f.rule_id} {f.rule_name}（全文）")
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
        shown = result.hints[:HINTS_MAX]
        if len(shown) < len(result.hints):
            out.append(f"共 {len(result.hints)} 处，列前 {len(shown)} 处：")
            out.append("")
        for f in shown:
            out.append(f"- {f.rule_id} {f.rule_name}（¶{f.para + 1}）")
        out.append("")
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
            "disclaimer": DISCLAIMER,
        },
        ensure_ascii=False,
        indent=2,
    )


# 自证文书引用的公开误伤案例（README「它不是什么」同源，改措辞须两边同步）
_APPEAL_CASES = (
    "老舍《林海》被商业检测工具判 99.9% AI（南都大数据研究院 2025 十款工具实测），"
    "朱自清《荷塘月色》被判 62.88% AI（南都湾财社 2024 报道），"
    "斯坦福实测七款英文检测器把非母语者托福作文平均误判 61.3%（Patterns 2023）"
)


def render_appeal(result: AnalysisResult, source: str) -> str:
    """被误伤自证文书（Markdown）：结论 → 逐句解释 → 复现命令 → 已知边界。

    给被 AIGC 检测误伤的作者拿去沟通用的：每处命中都可逐句对照原文自证，
    全部数字任何人都可在本地复现，文书自身就把"指数高≠AI 写的"讲清楚。
    """
    s = result.doc_stats
    cmd = f"human-vs-ai check {source} -p {result.profile} -f appeal"
    out: list[str] = []
    out.append("# 写作风格自查说明")
    out.append("")
    out.append(f"> 本文档由 human-vs-ai v{__version__} 本地引擎生成于 {time.strftime('%Y-%m-%d')}，"
               f"分析对象：`{source}`。")
    out.append("> human-vs-ai 是**风格分析器，不是 AI 检测器**：它只指出哪些句子"
               "用了高频写作模板，不判定、也无法判定文本是否由 AI 生成。")
    out.append("")
    out.append("## 一、检查结论")
    out.append("")
    if result.score:
        out.append(f"- AI 味指数：{round(result.score.index)} / 100（{result.profile} 场景）")
        out.append(f"- {_score_components(result.score)}")
    elif result.scoring_note:
        out.append(f"- AI 味指数：未出分（{result.scoring_note}）")
    else:
        out.append("- AI 味指数：未出分（文本不足 8 句，样本不够判定）")
    for line in _ood_lines(result):
        out.append(f"- {line}")
    out.append(f"- 规模：{s.n_paragraphs} 段 · {s.n_sentences} 句 · {s.n_chars} 字")
    if s.n_sentences >= 8:
        out.append(f"- 统计：句长 CV {_fmt(s.sentence_cv)} · 段长 CV {_fmt(s.para_len_cv)}"
                   f" · TTR {_fmt(s.ttr)} · 连接词 {_fmt(s.conn_density)} · 重复率 {_fmt(s.ngram_repeat)}")
    out.append("")
    out.append("## 二、逐句发现与解释")
    out.append("")
    if not result.findings:
        out.append("未发现模板化写作。")
    else:
        explained: set[str] = set()
        groups, doc_level = group_by_sentence(result.findings)
        out.append(f"共 {len(result.findings)} 处风格层命中（高 {result.n_high}"
                   f" · 中 {result.n_medium} · 低 {result.n_low}）。"
                   "每处含义：该句使用了某类高频写作模式——是文风特征，不是作者身份的证据。")
        out.append("")
        for group in groups:
            out.append(f"### {_group_title(group)}")
            out.append("")
            out.append(f"> {group[0].sentence}")
            out.append("")
            matches = [m for f in group for m in f.matches]
            out.append(f"命中模板词：{'、'.join(dict.fromkeys(matches))}")
            out.append("")
            for f in group:
                if f.rule_id in explained:
                    continue
                explained.add(f.rule_id)
                out.append(f"**为什么被标记（{f.rule_id}）**：{f.explanation}")
                if f.suggestion:
                    out.append("")
                    out.append(f"修改方向：{f.suggestion}")
                out.append("")
        for f in doc_level:
            out.append(f"### [{SEV_LABEL[f.severity]}] {f.rule_id} {f.rule_name}（全文层）")
            out.append("")
            out.append(f"命中特征：{f.matches[0]}")
            out.append("")
            out.append(f"**为什么被标记**：{f.explanation}")
            if f.suggestion:
                out.append("")
                out.append(f"修改方向：{f.suggestion}")
            out.append("")
    out.append("## 三、如何复核（可复现）")
    out.append("")
    out.append("本文档的全部数字可以在任何电脑上逐位复现：")
    out.append("")
    out.append("1. 安装：`pip install human-vs-ai`（纯本地运行，零网络调用）")
    out.append(f"2. 复现：`{cmd}`")
    out.append("3. 查每条规则的全套解释与出处：`human-vs-ai explain <规则ID>`"
               "（如 `human-vs-ai explain E-NEGA-01`）")
    out.append("4. 规则库、系数与校准数据全部公开：https://github.com/Glory0707/human-vs-ai")
    out.append("")
    out.append("## 四、已知边界（指数高不等于 AI 写的）")
    out.append("")
    out.append(f"- {_APPEAL_CASES}——文风工整的真文在风格指标上天然偏高，这是所有"
               "风格类工具的共同边界。")
    out.append("- 命中≠AI：真人同样会写「首先…其次…」「不仅…更是…」；"
               "单独任何一条都不构成作者身份的证据。")
    out.append("- 本工具不输出「AI 生成概率」，本文档也不能用于证明或豁免"
               "任何「AI 代写」指控——它给的是可人工复核的风格证据。")
    out.append("")
    return "\n".join(out)


def render(result: AnalysisResult, fmt: str) -> str:
    if fmt == "json":
        return render_json(result)
    if fmt == "md":
        return render_markdown(result)
    if fmt == "terminal":
        return render_terminal(result)
    raise ValueError(f"未知格式：{fmt}")
