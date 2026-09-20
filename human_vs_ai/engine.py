"""分析引擎：规则加载、逐句匹配、共现加权、全文统计判定。

两条铁律贯穿设计：

1. 推断不是判定。命中只说明"这句话出现了某种高频写作模式"，
   报告措辞永远是风格提示，不是 AI 判决——维基 Signs of AI writing
   开篇第一句就是"任何单一迹象都不能证明 AI 写作"。
2. 弱规则必须共现。真人也会写"首先…其次…"、也会用破折号；
   单独一次命中不是证据。标为 low 的规则只有当全文命中 ≥2 处
   才升格为正式发现，否则只作 hint 附在报告末尾——这是 linter
   与"误判机器"的分界线（Newby v. Adelphi：以单一分数定罪 lacks reason）。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import segment, stats

RULES_DIR = Path(__file__).parent / "rules"

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "hint": 0}

# YAML folded 块（>）把源码换行折叠成半角空格——中文行文里那是伪影
# （"研究 里 142 条"）。清"中文-空格-中文"及中文与破折号/引号之间的空格，
# 中英文之间的排版空格保留。
_CJK = "一-鿿　-ヿ＀-￯"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}])")
_QUOTE_DASH = "—“”‘’'"
_CJK_PD_GAP = re.compile(
    rf"(?<=[{_CJK}]) +(?=[{_QUOTE_DASH}])|(?<=[{_QUOTE_DASH}]) +(?=[{_CJK}])"
)


def _clean_prose(text: str) -> str:
    """规则文案的统一清洗：直引号配对换中文引号 + 去中文间折叠空格。"""
    return _CJK_PD_GAP.sub("", _CJK_GAP.sub("", _cn_quotes(text)))


def _cn_quotes(text: str) -> str:
    """prose 里的 ASCII 直引号按行配对换成中文引号——中文排版不用直引号。

    只处理引号数为偶数的行(奇数行说明有未配对用法,保留原样不猜)。
    """
    if '"' not in text:
        return text
    out = []
    for line in text.split("\n"):
        if line.count('"') % 2 == 0:
            buf = []
            open_q = True
            for ch in line:
                if ch == '"':
                    buf.append("“" if open_q else "”")
                    open_q = not open_q
                else:
                    buf.append(ch)
            out.append("".join(buf))
        else:
            out.append(line)
    return "\n".join(out)


@dataclass
class Rule:
    id: str
    name: str
    tier: str  # lexical / syntactic / structural / statistical
    scope: str  # sentence / shape / doc / lexicon（只供密度词表，不产生命中）
    severity: str  # high / medium / low
    patterns: list[re.Pattern] = field(default_factory=list)
    explanation: str = ""
    suggestion: str = ""
    example_before: str = ""
    example_after: str = ""
    references: list[str] = field(default_factory=list)
    doc_metric: str = ""  # scope=doc 时对应的 DocStats 字段
    doc_compare: str = ""  # below / above
    doc_threshold: float = math.nan
    doc_tiers: list = field(default_factory=list)  # [[chars上限, 阈值], ...] 按文本长度分档；None 上限=兜底档
    min_sentences: int = 8  # doc 统计判定的最小句数——短文本统计无意义，宁可不判
    human_ref: str = ""  # 人类基线的可读描述，进报告
    taste: str = ""  # 口味条目编号（T1…T12）——personal profile 用，指向 docs/taste_zhouao.md


@dataclass
class Finding:
    rule_id: str
    rule_name: str
    severity: str
    tier: str
    para: int  # 段号，doc 级为 -1
    sentence: str  # 命中原句；doc 级为空
    matches: list[str]  # 命中的模式文本
    explanation: str
    suggestion: str
    taste: str = ""  # 口味条目编号，指向 docs/taste_zhouao.md

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "tier": self.tier,
            "para": self.para,
            "sentence": self.sentence,
            "matches": self.matches,
            "explanation": self.explanation,
            "suggestion": self.suggestion,
            "taste": self.taste,
        }


@dataclass
class AnalysisResult:
    findings: list[Finding] = field(default_factory=list)
    hints: list[Finding] = field(default_factory=list)  # 弱规则孤立命中，仅供参考
    doc_stats: stats.DocStats = field(default_factory=stats.DocStats)
    profile: str = ""

    @property
    def n_high(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")

    @property
    def n_medium(self) -> int:
        return sum(1 for f in self.findings if f.severity == "medium")

    @property
    def n_low(self) -> int:
        return sum(1 for f in self.findings if f.severity == "low")


def available_profiles() -> list[str]:
    return sorted(p.stem for p in RULES_DIR.glob("*.yaml"))


def load_rules(profile: str) -> list[Rule]:
    path = RULES_DIR / f"{profile}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"profile '{profile}' 不存在。可用：{', '.join(available_profiles())}"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules: list[Rule] = []
    for item in data["rules"]:
        patterns = [re.compile(p) for p in item.get("patterns", [])]
        rules.append(
            Rule(
                id=item["id"],
                name=_clean_prose(item["name"]),
                tier=item.get("tier", "lexical"),
                scope=item.get("scope", "sentence"),
                severity=item.get("severity", "medium"),
                patterns=patterns,
                explanation=_clean_prose(item.get("explanation", "")).strip(),
                suggestion=_clean_prose(item.get("suggestion", "")).strip(),
                example_before=_cn_quotes(item.get("example_before", "")),
                example_after=_cn_quotes(item.get("example_after", "")),
                references=item.get("references", []),
                doc_metric=item.get("doc_metric", ""),
                doc_compare=item.get("doc_compare", ""),
                doc_threshold=float(item.get("doc_threshold", "nan")),
                doc_tiers=[(t[0], float(t[1])) for t in item.get("doc_tiers", [])],
                min_sentences=int(item.get("min_sentences", 8)),
                human_ref=_clean_prose(item.get("human_ref", "")),
                taste=item.get("taste", ""),
            )
        )
    return rules


_DENSITY_PREFIXES = ("L-CONN", "O-STK")  # 词表规则同时供全文密度统计的前缀


def connective_lexicon(rules: list[Rule]) -> set[str]:
    """从词表规则里抽出"纯词"集合喂给密度统计——保持单一来源。

    official profile 的 O-STK（强化副词）也走 conn_density 通道：
    那个字段在公文语境下的语义就是"工作副词密度"。
    """
    lex = set()
    for r in rules:
        if not r.id.startswith(tuple(_DENSITY_PREFIXES)):
            continue
        for p in r.patterns:
            if len(p.pattern) <= 8 and not any(
                c in p.pattern for c in "[](){}*+?.|\\"
            ):
                lex.add(p.pattern)
    return lex


def _doc_threshold(rule: Rule, n_chars: int) -> float:
    """按文本长度选阈值：doc_tiers 依次匹配 chars<上限，未命中用 doc_threshold。

    真人 CV 基线随文本变长系统性上移（T4.3 分档分析），单一阈值
    在短文本档误伤、长文本档过松，所以允许按长度分档。
    """
    for limit, thr in rule.doc_tiers:
        if limit is None or n_chars < limit:
            return thr
    return rule.doc_threshold


def analyze(text: str, profile: str = "academic") -> AnalysisResult:
    rules = load_rules(profile)
    doc = segment.split_document(text)
    para_texts = [[s.text for s in para] for para in doc]
    result = AnalysisResult(profile=profile)

    raw_hits: dict[str, list[Finding]] = {}

    # 逐句规则 + 段落形状规则（shape：判的不是内容是形状，比如"一句话总结段"）
    for pi, para in enumerate(doc):
        for sent in para:
            for rule in rules:
                if rule.scope != "sentence":
                    continue
                matches: list[str] = []
                for pat in rule.patterns:
                    m = pat.search(sent.text)
                    if m:
                        matches.append(m.group(0))
                if matches:
                    f = Finding(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        tier=rule.tier,
                        para=pi,
                        sentence=sent.text,
                        matches=matches,
                        explanation=rule.explanation,
                        suggestion=rule.suggestion,
                        taste=rule.taste,
                    )
                    raw_hits.setdefault(rule.id, []).append(f)
        for rule in rules:
            if rule.scope != "shape":
                continue
            if rule.doc_metric == "one_liner" and len(para) == 1 and len(para[0].text) <= 40:
                raw_hits.setdefault(rule.id, []).append(
                    Finding(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        tier=rule.tier,
                        para=pi,
                        sentence=para[0].text,
                        matches=[f"独句段（{len(para[0].text)} 字）"],
                        explanation=rule.explanation,
                        suggestion=rule.suggestion,
                        taste=rule.taste,
                    )
                )

    # 共现加权：low 规则全文 <2 处命中 → 降为 hint
    for rid, hits in raw_hits.items():
        rule_sev = hits[0].severity
        if rule_sev == "low" and len(hits) < 2:
            result.hints.extend(hits)
        else:
            result.findings.extend(hits)

    # 全文统计 + doc 级规则
    result.doc_stats = stats.compute_doc_stats(
        para_texts, connective_lexicon=connective_lexicon(rules)
    )
    for rule in rules:
        if rule.scope != "doc" or not rule.doc_metric:
            continue
        # TTR 阈值按词级口径标定；没装 jieba 时是字级 2-gram 口径，数值不可比，跳过不判
        if rule.doc_metric == "ttr" and not stats._HAS_JIEBA:
            continue
        if result.doc_stats.n_sentences < rule.min_sentences:
            continue  # 短文本统计无意义——宁可不判
        value = getattr(result.doc_stats, rule.doc_metric, math.nan)
        if value != value:  # NaN：样本太少，不判
            continue
        threshold = _doc_threshold(rule, result.doc_stats.n_chars)
        hit = (
            value < threshold
            if rule.doc_compare == "below"
            else value > threshold
        )
        if hit:
            result.findings.append(
                Finding(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    severity=rule.severity,
                    tier=rule.tier,
                    para=-1,
                    sentence="",
                    matches=[f"{rule.doc_metric}={value:.3f}（阈值 {threshold:.2f}）"],
                    explanation=rule.explanation,
                    suggestion=rule.suggestion,
                    taste=rule.taste,
                )
            )

    result.findings.sort(
        key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.para)
    )
    return result
