"""HTML 报告出口：单文件静态页（内联样式，无 JS，可打印可分享）。

内容与 terminal/md/json 同源，只是出口形态不同——文纸·朱批的
排版语言（印章指数、批注卡、命中高亮）随报告一起带走。
"""
from __future__ import annotations

import html as _html
from collections import OrderedDict

from . import __version__
from .engine import AnalysisResult
from .report import _DISCLAIMER, _group_by_sentence, _group_top, _taste_suffix, stats_lines

_SEV_COLOR = {"high": "#B3351F", "medium": "#9C7414", "low": "#2E7D6E"}
_SEV_LABEL = {"high": "高", "medium": "中", "low": "低"}
_SCORE_LABEL = {"hit_density": "规则", "sentence_cv": "节奏", "ttr": "词汇",
                "ngram_repeat": "重复", "conn_density": "连接词"}

_CSS = """
:root { --paper:#FBF9F4; --ink:#26241F; --ink2:#6F6A5E; --ink3:#9A9484;
        --hair:#E5E0D1; --chip:#F1EDE1; --soft:#F4F0E6;
        --mark:rgba(194,64,42,.12); --markline:rgba(194,64,42,.45); }
* { margin:0; padding:0; box-sizing:border-box; }
body { font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
       background:var(--paper); color:var(--ink); max-width:860px;
       margin:0 auto; padding:32px 24px 48px; }
header { border-bottom:1px solid var(--hair); padding-bottom:12px; margin-bottom:18px;
         display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.wordmark { font-family:"Noto Serif SC","Source Han Serif SC","Songti SC","STSong","SimSun",serif;
            font-weight:700; font-size:17px; }
.meta { color:var(--ink3); font-size:11px; letter-spacing:.05em; }
.score-row { display:flex; align-items:center; gap:14px; margin:6px 0 14px; }
.seal { flex:none; display:inline-flex; flex-direction:column; align-items:center;
        justify-content:center; width:62px; height:62px; border:2px solid currentColor;
        border-radius:6px; transform:rotate(-2deg); line-height:1;
        box-shadow:inset 0 0 0 1.5px var(--paper), inset 0 0 0 2.5px currentColor; }
.seal .n { font:700 27px Georgia,"Times New Roman",serif; padding-top:2px; }
.seal .u { font-size:8.5px; letter-spacing:.2em; margin-top:3px; font-weight:600; }
.score-main .t { font-weight:700; font-size:14px; }
.score-main .sub { display:block; font-size:11px; color:var(--ink3); margin-top:3px; }
.stats { border-bottom:1px solid var(--hair); padding-bottom:12px; }
.stats div { font-size:12.5px; color:var(--ink2); }
.summary { font-family:"Noto Serif SC","Songti SC","SimSun",serif; font-weight:700;
           font-size:15px; padding:14px 0 8px; }
.found { border-left:2px solid var(--hair); padding:8px 0 8px 14px;
         border-bottom:1px solid var(--hair); }
.found:last-of-type { border-bottom:none; }
.head { font-size:13px; }
.sev-tag { display:inline-block; font-size:10.5px; font-weight:600; border:1px solid;
           border-radius:3px; padding:0 4px; margin-right:8px; }
.loc { color:var(--ink3); font-size:10.5px; margin-left:8px; }
blockquote { margin:6px 0 4px; padding:2px 0 2px 12px; border-left:2px solid var(--hair);
             color:var(--ink2); }
.match { font-size:12px; color:var(--ink3); margin:2px 0 6px; }
.match code { background:var(--chip); color:var(--ink2); padding:0 4px; border-radius:2px; }
.tip { color:#2E7D6E; margin-top:3px; }
mark { background:var(--mark); color:inherit; padding:0 1px; box-shadow:0 1px 0 var(--markline); }
.hints { margin-top:14px; padding-top:10px; border-top:1px solid var(--hair);
         font-size:12px; color:var(--ink3); }
.hints div { margin-top:2px; }
.disclaimer { margin-top:18px; padding:10px 14px; background:var(--soft);
              font-size:11px; color:var(--ink2); border-radius:3px; }
@media print { body { padding:0; } }
"""


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _hi_sentence(sent: str, matches: list[str]) -> str:
    """命中词高亮：与 web/render.js hiSentence 同口径（区间合并后切片）。"""
    spans: list[list[int]] = []
    for m in matches:
        if not m:
            continue
        start = 0
        while (idx := sent.find(m, start)) >= 0:
            spans.append([idx, idx + len(m)])
            start = idx + len(m)
    if not spans:
        return _esc(sent)
    spans.sort(key=lambda s: (s[0], -s[1]))
    merged = [spans[0][:]]
    for s in spans[1:]:
        if s[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s[1])
        else:
            merged.append(s[:])
    out, pos = [], 0
    for a, b in merged:
        out.append(_esc(sent[pos:a]))
        out.append(f"<mark>{_esc(sent[a:b])}</mark>")
        pos = b
    out.append(_esc(sent[pos:]))
    return "".join(out)


def _score_html(result: AnalysisResult) -> str:
    if result.score:
        s = result.score
        idx = round(s.index)
        band = "high" if s.index > s.human_p90 else ("medium" if s.index > s.human_p50 else "low")
        comps = " · ".join(
            f"{_SCORE_LABEL.get(f, f)} {v:+.0f}" for f, v in s.components.items())
        return (f'<div class="score-row">'
                f'<span class="seal" style="color:{_SEV_COLOR[band]}">'
                f'<span class="n">{idx}</span><span class="u">AI味指数</span></span>'
                f'<span class="score-main"><span class="t">{idx} / 100</span>'
                f'<span class="sub">风格综合分 · 真人 p50≈{s.human_p50} / p90≈{s.human_p90}'
                f' · 构成：{comps}</span></span></div>')
    if result.scoring_note:
        return (f'<div class="score-row"><span class="score-main">'
                f'<span class="sub">AI 味指数 —（{_esc(result.scoring_note)}）</span>'
                f'</span></div>')
    return ""


def _finding_card(sev: str, title: str, loc: str, sentence: str,
                  matches: list[str], body: str) -> str:
    color = _SEV_COLOR[sev]
    parts = [f'<div class="found" style="border-left-color:{color}">']
    parts.append(f'<div class="head"><span class="sev-tag" style="color:{color}">'
                 f'{_SEV_LABEL[sev]}</span>{title}<span class="loc">{loc}</span></div>')
    if sentence:
        parts.append(f"<blockquote>{_hi_sentence(sentence, matches)}</blockquote>")
    if matches:
        shown = "、".join(dict.fromkeys(matches))
        parts.append(f'<div class="match">命中：<code>{_esc(shown)}</code></div>')
    parts.append(body)
    parts.append("</div>")
    return "\n".join(parts)


def render_html(result: AnalysisResult) -> str:
    out = [f"<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">",
           f"<title>human-vs-ai 分析报告（{result.profile}）</title>",
           f"<style>{_CSS}</style>\n</head>\n<body>",
           f"<header><span class=\"wordmark\">human-vs-ai</span>"
           f"<span class=\"meta\">v{__version__} · {result.profile}</span></header>"]
    out.append(_score_html(result))
    out.append('<div class="stats">'
               + "".join(f"<div>{_esc(line)}</div>" for line in stats_lines(result)
                         if not line.startswith("AI 味指数") and not line.startswith("构成："))
               + "</div>")

    n = len(result.findings)
    out.append(f'<div class="summary">{"发现 " + str(n) + " 处" if n else "未发现模板化写作"}</div>')
    explained: set[str] = set()
    groups, doc_level = _group_by_sentence(result.findings)
    for group in groups:
        top = _group_top(group)
        ids = " + ".join(dict.fromkeys(f.rule_id for f in group))
        names = " + ".join(dict.fromkeys(f.rule_name for f in group))
        taste = _taste_suffix(group)
        body_parts = []
        for f in group:
            if f.rule_id in explained:
                continue
            explained.add(f.rule_id)
            body_parts.append(f"<div>{_esc(f.explanation)}</div>")
            if f.suggestion:
                body_parts.append(f'<div class="tip">→ {_esc(f.suggestion)}</div>')
        out.append(_finding_card(
            top, f"{_esc(ids)} {_esc(names)}{_esc(taste)}",
            f"¶{group[0].para + 1}", group[0].sentence,
            [m for f in group for m in f.matches], "".join(body_parts)))
    for f in doc_level:
        body = f"<div>{_esc(f.explanation)}</div>"
        if f.suggestion:
            body += f'<div class="tip">→ {_esc(f.suggestion)}</div>'
        taste = f" · {f.taste}" if f.taste else ""
        out.append(_finding_card(
            f.severity, f"{_esc(f.rule_id)} {_esc(f.rule_name)}{_esc(taste)}", "全文",
            "", list(f.matches), body))

    if result.hints:
        shown = result.hints[:12]
        out.append(f'<div class="hints"><div>另有 {len(result.hints)} 处弱命中'
                   f'{"（列前 %d 处）" % len(shown) if len(shown) < len(result.hints) else ""}</div>')
        for f in shown:
            out.append(f"<div>· {_esc(f.rule_id)} {_esc(f.rule_name)}"
                       f"（¶{f.para + 1}）</div>")
        out.append("</div>")
    out.append(f'<div class="disclaimer">{_DISCLAIMER}</div>')
    out.append("</body>\n</html>")
    return "\n".join(out)
