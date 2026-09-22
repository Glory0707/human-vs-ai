"""SARIF 2.1.0 出口：GitHub code scanning 等平台直接可吃。

行定位是 best-effort：发现里的原句是剥掉 Markdown 标记的干净文本，
把原句与源文件各行都归一化到"汉字+字母数字"再子串匹配，命中的第一行
即 startLine。匹配不上（整段重写、表格重排）就不给 region——SARIF 允许
没有 region 的 result，平台回落到文件级。doc 级发现（para=-1）定位全文。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import __version__, engine
from .engine import AnalysisResult

INFORMATION_URI = "https://github.com/Glory0707/human-vs-ai"

# 严重级 → SARIF level
_LEVEL = {"high": "error", "medium": "warning", "low": "note"}


def _norm(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or "一" <= ch <= "鿿")


def _locate_line(text: str, needle: str) -> int | None:
    n = _norm(needle)[:16]
    if len(n) < 4:
        return None
    for i, line in enumerate(text.splitlines(), 1):
        if n in _norm(line):
            return i
    return None


def _rules_array(profile: str) -> list[dict]:
    rules = []
    for r in engine.load_rules(profile):
        entry = {
            "id": r.id,
            "name": r.id,
            "shortDescription": {"text": r.name},
            "defaultConfiguration": {"level": _LEVEL.get(r.severity, "note")},
            "properties": {"severity": r.severity, "tier": r.tier},
        }
        if r.explanation:
            entry["fullDescription"] = {"text": r.explanation}
        if r.suggestion:
            entry["help"] = {"text": r.suggestion}
        rules.append(entry)
    return rules


def _message(f) -> str:
    head = f"{f.rule_id} {f.rule_name}"
    if f.sentence:
        show = f.sentence if len(f.sentence) <= 80 else f.sentence[:77] + "…"
        return f"{head}：「{show}」"
    return f"{head}（全文）：{f.matches[0] if f.matches else ''}"


def _result(f, rule_index: dict[str, int], uri: str, line: int | None) -> dict:
    fp = hashlib.sha1(
        f"{f.rule_id}\x00{f.para}\x00{f.sentence}".encode("utf-8")).hexdigest()[:16]
    res = {
        "ruleId": f.rule_id,
        "ruleIndex": rule_index[f.rule_id],
        "level": _LEVEL.get(f.severity, "note"),
        "message": {"text": _message(f)},
        "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": uri},
                **({"region": {"startLine": line}} if line else {}),
            }
        }],
        "partialFingerprints": {"humanVsAiOccurrence/v1": fp},
    }
    if f.suggestion:
        res["message"]["text"] += f" 建议：{f.suggestion}"
    return res


def render(entries: list[tuple[str, str, AnalysisResult]], profile: str) -> str:
    """entries: [(路径, 原文, 分析结果), ...]——单文件与批量共用一个 run。"""
    rules = _rules_array(profile)
    rule_index = {r["id"]: i for i, r in enumerate(rules)}
    results = []
    artifacts = []
    seen_uri: dict[str, int] = {}
    for path, text, result in entries:
        uri = Path(path).as_posix()
        if uri not in seen_uri:
            seen_uri[uri] = len(artifacts)
            artifacts.append({"location": {"uri": uri}})
        for f in result.findings:
            line = None if f.para < 0 else _locate_line(text, f.sentence)
            results.append(_result(f, rule_index, uri, line))
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "human-vs-ai",
                "informationUri": INFORMATION_URI,
                "version": __version__,
                "rules": rules,
            }},
            "artifacts": artifacts,
            "results": results,
        }],
    }
    return json.dumps(sarif, ensure_ascii=False)
