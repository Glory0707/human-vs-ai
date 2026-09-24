"""改进闭环：改前 vs 改后——哪几类问题消了、哪几类新犯、指数往哪走。

check 回答"哪里像模板"，diff 回答"你改的这一版有没有效"。按 rule_id
聚合出现次数对比（findings + hints 合并计数：一次真实修复会让命中从
正式发现降成弱命中再消失，只数 findings 会把"改到一半"误读成没改），
不按句匹配——句子换了措辞照样算消了，规则粒度才是"这类问题改掉没有"
的正确口径。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from . import __version__, engine
from .engine import AnalysisResult
from .report import DISCLAIMER, SCORE_LABEL

# 出现次数变化 → 状态（排序即展示序：消了的排最前）
_STATUS_ORDER = {"resolved": 0, "less": 1, "introduced": 2, "more": 3}
_STATUS_LABEL = {"resolved": "已消除", "less": "减少", "introduced": "新增", "more": "增加"}


@dataclass
class RuleDelta:
    rule_id: str
    rule_name: str
    before: int
    after: int

    @property
    def status(self) -> str:
        if self.before > 0 and self.after == 0:
            return "resolved"
        if self.before == 0 and self.after > 0:
            return "introduced"
        if self.after > self.before:
            return "more"
        return "less"


@dataclass
class DiffResult:
    profile: str
    old: AnalysisResult
    new: AnalysisResult
    deltas: list[RuleDelta]

    def _index(self, r: AnalysisResult):
        return round(r.score.index) if r.score else None

    @property
    def index_before(self):
        return self._index(self.old)

    @property
    def index_after(self):
        return self._index(self.new)

    @property
    def component_deltas(self) -> dict[str, dict]:
        feats = list(dict.fromkeys(
            list(self.old.score.components if self.old.score else {})
            + list(self.new.score.components if self.new.score else {})))
        out = {}
        for feat in feats:
            b = self.old.score.components.get(feat, 0.0) if self.old.score else 0.0
            a = self.new.score.components.get(feat, 0.0) if self.new.score else 0.0
            out[feat] = {"before": round(b, 1), "after": round(a, 1),
                         "delta": round(a - b, 1)}
        return out


def compute(old_text: str, new_text: str, profile: str) -> DiffResult:
    old = engine.analyze(old_text, profile)
    new = engine.analyze(new_text, profile)

    def occurrences(r: AnalysisResult) -> tuple[dict[str, int], dict[str, str]]:
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        for f in r.findings + r.hints:
            counts[f.rule_id] = counts.get(f.rule_id, 0) + 1
            names.setdefault(f.rule_id, f.rule_name)
        return counts, names

    old_c, old_names = occurrences(old)
    new_c, new_names = occurrences(new)
    all_names = {**old_names, **new_names}
    deltas = [
        RuleDelta(rid, all_names.get(rid, ""), old_c.get(rid, 0), new_c.get(rid, 0))
        for rid in set(old_c) | set(new_c)
        if old_c.get(rid, 0) != new_c.get(rid, 0)
    ]
    deltas.sort(key=lambda d: (_STATUS_ORDER[d.status], -abs(d.after - d.before), d.rule_id))
    return DiffResult(profile=profile, old=old, new=new, deltas=deltas)


def _idx_str(v) -> str:
    return "—" if v is None else str(v)


def render_terminal(d: DiffResult) -> str:
    use_color = sys.stdout.isatty()
    C = (lambda code, s: f"\033[{code}m{s}\033[0m" if use_color else s)
    GOOD, BAD, DIM = "32", "31", "90"

    out = [C("1", f"human-vs-ai v{__version__} · diff（{d.profile}）"), "─" * 46]
    ib, ia = d.index_before, d.index_after
    if ib is not None and ia is not None:
        delta = ia - ib
        color = GOOD if delta < 0 else (BAD if delta > 0 else DIM)
        out.append(f"AI 味指数 {_idx_str(ib)} → {_idx_str(ia)}（"
                   + C(color, f"{delta:+d}") + "）"
                   + C(DIM, f" · 真人锚点 p50 {d.new.score.human_p50}"
                       if d.new.score else ""))
    else:
        out.append(f"AI 味指数 {_idx_str(ib)} → {_idx_str(ia)}（样本不足或未校准，不出分）")
    comp = d.component_deltas
    if comp:
        parts = []
        for feat, v in comp.items():
            parts.append(f"{SCORE_LABEL.get(feat, feat)} {v['delta']:+.0f}")
        out.append("构成变化：" + " · ".join(parts))
    out.append(f"发现 {len(d.old.findings)} 处 → {len(d.new.findings)} 处")
    out.append("")

    if not d.deltas:
        out.append(C("32", "两类命中没有变化。"))
    for delta in d.deltas:
        color = GOOD if delta.status in ("resolved", "less") else BAD
        tag = _STATUS_LABEL[delta.status]
        out.append(C(color,
                     f"[{tag}] {delta.rule_id} {delta.rule_name} "
                     f"{delta.before}→{delta.after}"))
    out.append("")
    out.append(C(DIM, "─" * 46))
    out.append(C(DIM, DISCLAIMER))
    return "\n".join(out)


def render_md(d: DiffResult) -> str:
    out = [f"# human-vs-ai diff（{d.profile}）", ""]
    ib, ia = d.index_before, d.index_after
    if ib is not None and ia is not None:
        out.append(f"AI 味指数：{ib} → {ia}（{ia - ib:+d}）")
    else:
        out.append(f"AI 味指数：{_idx_str(ib)} → {_idx_str(ia)}（样本不足或未校准，不出分）")
    comp = d.component_deltas
    if comp:
        parts = [f"{SCORE_LABEL.get(f, f)} {v['delta']:+.0f}" for f, v in comp.items()]
        out.append(f"构成变化：{' · '.join(parts)}")
    out.extend([f"发现：{len(d.old.findings)} 处 → {len(d.new.findings)} 处", ""])
    if d.deltas:
        out.extend(["| 变化 | 规则 | 次数 |", "|---|---|---|"])
        for delta in d.deltas:
            out.append(f"| {_STATUS_LABEL[delta.status]} | {delta.rule_id} "
                       f"{delta.rule_name} | {delta.before}→{delta.after} |")
    else:
        out.append("两类命中没有变化。")
    out.extend(["", "---", "", DISCLAIMER])
    return "\n".join(out)


def render_json(d: DiffResult) -> str:
    return json.dumps(
        {
            "tool": "human-vs-ai", "version": __version__, "profile": d.profile,
            "index": {"before": d.index_before, "after": d.index_after,
                      "delta": (d.index_after - d.index_before)
                      if None not in (d.index_before, d.index_after) else None},
            "findings": {"before": len(d.old.findings), "after": len(d.new.findings)},
            "components": d.component_deltas,
            "rules": [
                {"rule_id": x.rule_id, "rule_name": x.rule_name,
                 "before": x.before, "after": x.after, "status": x.status}
                for x in d.deltas],
            "disclaimer": DISCLAIMER,
        },
        ensure_ascii=False, indent=2)


def render(d: DiffResult, fmt: str) -> str:
    if fmt == "json":
        return render_json(d)
    if fmt == "md":
        return render_md(d)
    if fmt == "terminal":
        return render_terminal(d)
    raise ValueError(f"未知格式：{fmt}")
