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
from functools import lru_cache
from pathlib import Path

import yaml

from . import ood, segment, stats

RULES_DIR = Path(__file__).parent / "rules"

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "hint": 0}

# YAML folded 块（>）把源码换行折叠成半角空格——中文行文里那是伪影
# （"研究 里 142 条"）。清"中文-空格-中文"与中文标点两侧的空格
# （"。 2023"、"—— “"都是折叠伪影；中文标点旁不存在合法排版空格），
# 普通汉字与英文单词之间的排版空格保留。
_CJK = "一-鿿　-ヿ＀-￯"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}])")
_CJK_PUNCT = "　-〿＀-￯—‘’“”"
_CJK_PUNCT_GAP = re.compile(rf"(?<=[{_CJK_PUNCT}]) +| +(?=[{_CJK_PUNCT}])")


def _clean_prose(text: str) -> str:
    """规则文案的统一清洗：直引号配对换中文引号 + 去中文间折叠空格。"""
    return _CJK_PUNCT_GAP.sub("", _CJK_GAP.sub("", _cn_quotes(text)))


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
class Score:
    """AI 味指数：规则命中密度 + 全文统计的逻辑回归综合分（0-100）。

    系数在 profile YAML 的 scoring 段（校准只改数据的纪律），拟合与
    分层验证见 tools/fit_score.py 与 _qa/score-fit.json。它回答"这篇
    整体上模板腔有多重"，逐句归因仍由 findings 承担——分数不许单独
    定罪，免责声明始终随行。
    """

    index: float  # 0-100
    components: dict[str, float]  # 各特征贡献 coef*value（显示与解释用）
    corpus: str = ""
    auroc: float = math.nan
    human_p50: int = 0  # 校准语料真人指数分位——给读分数的人一个锚
    human_p90: int = 0

    def to_dict(self) -> dict:
        return {
            "index": round(self.index, 1),
            "components": {k: round(v, 1) for k, v in self.components.items()},
            "human_p50": self.human_p50,
            "human_p90": self.human_p90,
            "corpus": self.corpus,
            "auroc": self.auroc,
        }


@dataclass
class AnalysisResult:
    findings: list[Finding] = field(default_factory=list)
    hints: list[Finding] = field(default_factory=list)  # 弱规则孤立命中，仅供参考
    doc_stats: stats.DocStats = field(default_factory=stats.DocStats)
    profile: str = ""
    score: Score | None = None  # <8 句或该 profile 未校准时为 None
    # 8 句以上却没出分时给一句原因（该文体未校准）；<8 句保持空——
    # 短文本本来就不展示统计，多一行解释反而吵（v0.9.1 的教训）
    scoring_note: str = ""
    # 域外文体（"classical"/"verse"）与文种（"issuance-notice"/"approval-reply"）：
    # 两类都会让指数失真，报告须随行提示
    ood: list[str] = field(default_factory=list)
    # 段落热度：每段门控前加权密度（与全文 hit_density 同口径），混写文本定位用
    para_heat: list[dict] = field(default_factory=list)

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


@lru_cache(maxsize=None)
def load_rules(profile: str) -> list[Rule]:
    """加载并编译一个 profile 的规则。

    结果按 profile 缓存（与 JS 端 compileRules 的 WeakMap 缓存同策略）：
    网页逐键分析、批量评测、长循环拟合都不再重复编译正则。
    代价是同进程内改 YAML 不生效——校准流程本来就是"改完重跑"。
    """
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
                taste=item.get("taste", ""),
            )
        )
    return rules


_DENSITY_PREFIXES = ("L-CONN", "O-STK")  # 词表规则同时供全文密度统计的前缀

# 评分特征权重：严重级 → 加权密度系数（拟合工具 fit_score*.py 直接引用本表）
_SCORE_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}


def compute_para_heat(para_texts: list[list[str]], findings: list[Finding],
                      hints: list[Finding]) -> list[dict]:
    """段落级 AI 味热度：每段门控前加权密度（与全文 hit_density 同口径）。

    人改 AI 初稿的混写文本里，全篇一个分数必然失真——热度把"哪一段
    最像 AI"指出来。density = 该段命中权重和 / 段句数；level 是可读
    分档（≥1.0 平均每句都有命中 / ≥0.5 / >0 / 无命中不出现在列表里）。
    doc 级发现（para=-1）是全文属性，不摊进任何段落。
    """
    weighted: dict[int, float] = {}
    for f in findings:
        if f.para >= 0:
            weighted[f.para] = weighted.get(f.para, 0.0) + _SCORE_WEIGHT.get(f.severity, 1.0)
    for f in hints:
        if f.para >= 0:
            weighted[f.para] = weighted.get(f.para, 0.0) + _SCORE_WEIGHT.get(f.severity, 1.0)
    heat = []
    for pi, para in enumerate(para_texts):
        n = len(para)
        if not n or pi not in weighted:
            continue
        density = weighted[pi] / n
        level = "high" if density >= 1.0 else ("medium" if density >= 0.5 else "low")
        # excerpt 供交互端在原稿中定位该段（首句前 16 字，码点口径）
        heat.append({"para": pi, "n_sents": n, "density": density, "level": level,
                     "excerpt": para[0][:16]})
    heat.sort(key=lambda h: -h["density"])
    return heat
# scoring 段里的元字段，不是特征
_SCORING_META = ("corpus", "auroc", "auroc_holdout", "human_p50", "human_p90",
                 "genre_ood", "genre_scoring")


def load_scoring(profile: str) -> dict | None:
    """读 profile YAML 的 scoring 段；没有（未校准）返回 None——宁缺毋滥。"""
    path = RULES_DIR / f"{profile}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("scoring")


def _pick_scoring(scoring: dict, n_chars: int) -> dict:
    """按正文字数选系数组：scoring.tiers = [{min_chars, intercept, <特征>…}]。

    最后一个满足 min_chars ≤ n_chars 的层生效；都不满足或无 tiers 时用
    全局系数。元字段（corpus/auroc/分位锚点）始终取全局。与 _doc_threshold
    的分档思想同源——真人基线随长度变，评分系数也一样（v0.12.0 全量
    分档拟合：长文专用系数 holdout 0.975 vs 全局 0.946）。
    """
    tiers = scoring.get("tiers")
    if not tiers:
        return scoring
    picked = dict(scoring)
    for tier in tiers:
        min_chars = tier.get("min_chars")
        if min_chars is None or n_chars >= min_chars:
            picked = {k: v for k, v in scoring.items()
                      if k not in _SCORING_META and k != "tiers"}
            picked.update(tier)
            picked.pop("min_chars", None)
    picked.pop("tiers", None)
    return picked


def compute_score(
    doc_stats: stats.DocStats,
    findings: list[Finding],
    hints: list[Finding],
    scoring: dict,
) -> Score | None:
    """综合评分：门控前的规则加权密度 + 全文统计，过逻辑回归映射 0-100。

    规则特征用未门控密度——共现门控是"逐句指控"的纪律（单个弱命中
    不许告一条句子），文档级聚合保留幅度信息更有效（消融：0.847 vs 0.810）。
    TTR 直接用 doc_stats.ttr：全文唯一切分口径是字级 2-gram，与 JS 端
    逐位一致。短文本（<8 句）不出分。系数可按长度分档（tiers）。
    """
    if not scoring:
        return None
    n = doc_stats.n_sentences
    if n < 8:
        return None
    hit_density = (
        sum(_SCORE_WEIGHT.get(f.severity, 1.0) for f in findings + hints if f.para >= 0) / n
    )
    values = {
        "hit_density": hit_density,
        "sentence_cv": doc_stats.sentence_cv,
        "ttr": doc_stats.ttr,
        "ngram_repeat": doc_stats.ngram_repeat,
        "conn_density": doc_stats.conn_density,
    }
    picked = _pick_scoring(scoring, doc_stats.n_chars)
    z = float(picked.get("intercept", 0.0))
    components: dict[str, float] = {}
    for feat, coef in picked.items():
        if feat in _SCORING_META:
            continue
        v = values.get(feat)
        if v is None or v != v:  # 该 profile 没有此特征或值为 NaN（无词表等）
            continue
        components[feat] = float(coef) * v
        z += components[feat]
    z = max(min(z, 30.0), -30.0)
    return Score(
        index=100.0 / (1.0 + math.exp(-z)),
        components=components,
        corpus=str(scoring.get("corpus", "")),
        auroc=float(scoring.get("auroc", math.nan)),
        human_p50=int(scoring.get("human_p50", 0)),
        human_p90=int(scoring.get("human_p90", 0)),
    )


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


def _finding(rule: Rule, para: int, sentence: str, matches: list[str]) -> Finding:
    """规则命中 → Finding：文案字段统一从规则带出，调用处只给定位信息。"""
    return Finding(
        rule_id=rule.id,
        rule_name=rule.name,
        severity=rule.severity,
        tier=rule.tier,
        para=para,
        sentence=sentence,
        matches=matches,
        explanation=rule.explanation,
        suggestion=rule.suggestion,
        taste=rule.taste,
    )


def analyze(text: str, profile: str = "academic") -> AnalysisResult:
    rules = load_rules(profile)
    doc = segment.split_document(text)
    para_texts = [[s.text for s in block.sents] for block in doc]
    result = AnalysisResult(profile=profile)
    # 域外判定：全文一遍 + 逐段一遍聚合——白话引用文言段时全文统计被
    # 稀释（实测混排漏检），逐段能抓到；C-ReD 全量验证分段版误报率不变
    all_para = [s for para in para_texts for s in para]
    result.ood = ood.detect(all_para)
    for para in para_texts:
        for kind in ood.detect(para):
            if kind not in result.ood:
                result.ood.append(kind)

    # 文种域外：只对声明了 genre_ood 的 profile 判。开关放 scoring 段（数据侧）
    # 而不是按 profile 名硬编码——JS 端拿不到 profile 名、只拿得到 scoring 对象，
    # 放数据里两端才可能同构（判据的标定见 _qa/generalization-check.md）。
    # genre_scoring 按命中文种接管出分：suppress=true 抑制出分（score=None），
    # 否则整段系数替换（文种内二次校准，v0.20.0；两分支共用一套数据形状）
    scoring = load_scoring(profile)
    score_scoring = scoring
    if scoring and scoring.get("genre_ood"):
        for kind in ood.detect_genre(all_para):
            if kind not in result.ood:
                result.ood.append(kind)
        gcfg = scoring.get("genre_scoring") or {}
        gkind = next((k for k in result.ood if k in gcfg), None)
        if gkind is not None:
            cfg = gcfg[gkind]
            if cfg.get("suppress"):
                score_scoring = None
                result.scoring_note = "该文种未校准评分"
            else:
                score_scoring = dict(cfg)

    raw_hits: dict[str, list[Finding]] = {}
    # 逐句规则 + 段落形状规则（shape：判的不是内容是形状，比如"一句话总结段"）
    for pi, block in enumerate(doc):
        for sent in block.sents:
            for rule in rules:
                if rule.scope != "sentence":
                    continue
                matches: list[str] = []
                for pat in rule.patterns:
                    m = pat.search(sent.text)
                    if m:
                        matches.append(m.group(0))
                if matches:
                    raw_hits.setdefault(rule.id, []).append(
                        _finding(rule, pi, sent.text, matches)
                    )
        # 独句总结段只看普通段：列表/表格的条目天然又短又独立，
        # 判成"盖章段"是格式误伤
        if block.kind != "para":
            continue
        para = block.sents
        for rule in rules:
            if rule.scope != "shape":
                continue
            if rule.doc_metric == "one_liner" and len(para) == 1 and len(para[0].text) <= 40:
                raw_hits.setdefault(rule.id, []).append(
                    _finding(rule, pi, para[0].text, [f"独句段（{len(para[0].text)} 字）"])
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
                _finding(rule, -1, "", [f"{rule.doc_metric}={value:.3f}（阈值 {threshold:.2f}）"])
            )

    result.findings.sort(
        key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.para)
    )
    result.score = compute_score(
        result.doc_stats,
        result.findings,
        result.hints,
        score_scoring,
    )
    # 文种抑制已提前给过 scoring_note（"该文种未校准评分"）——profile 级
    # scoring 存在时通用分支不触发，不会覆盖
    if result.score is None and scoring is None and result.doc_stats.n_sentences >= 8:
        result.scoring_note = "该文体未校准评分"
    result.para_heat = compute_para_heat(para_texts, result.findings, result.hints)
    return result
