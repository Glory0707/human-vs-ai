"""改写器：按个人口味规则给出逐句的 删 / 改 / 保留 三档建议。

最高优先准则是删减哲学（docs/taste_zhouao.md R1，事件 c1ac954fc613 原话）：

    不是一味的删减，而是该多说时多说，该少说时少说，重要数据和结论要保留。

工程含义落在 classify() 的判定顺序上：**先看是不是"重要内容"**——含数字、
结论、事实断言的句子一律进"保留"档，腔调规则不覆盖它。一个含数据的句子
哪怕句式不好，也不该被整句删掉；反过来，纯腔调句即使写得漂亮也进"删"档。

第二准则（R2）：功能说明腔整句删，不压缩不改写——这是本人的实际处理方式。

改写候选是规则化产出（不调模型）：腔调句给"减"的操作，缺具体名词的地方
给方向提示。真正需要的梗只能人来补——模块会明说这一点，不假装会造梗。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import engine

# 三档
KEEP = "保留"
REWRITE = "改"
DELETE = "删"

# R1：重要内容的判据——数字、结论、事实断言。数字显式列 ASCII+全角
# （与 JS 端字符类逐字一致；JS 的 \d 不认全角，中文文案里全角数字常见）。
# 注意不含"三篇"这类中文数词+量词：文案里的量词不是数据，判成"重要"会
# 把该改的句子放进保留档（实测踩过：一条被毙稿因此漏判）。
_IMPORTANT = re.compile(
    r"[0-9０-９]|%|％|[一二三四五六七八九十百千万]+(倍|万|亿|人天|分钟)"
    r"|(结论|结果表明|数据显示|实测|验证|复现|报错|错误码|失败率|通过率|达标|未达标)"
)
# R3：具体名词与梗——我的定稿里 60% 含具体名词，被毙稿只有 19%，
# 这是判别力最强的单项。缺这两样且无腔调命中的句子仍进"改"档：
# 方向不是"删"，是"补一个能承载信息的实物"。
_NOUN = re.compile(
    r"(DDL|deadline|组会|参考文献|文献|论文|paper|Paper|accept|数据|导师|大佬"
    r"|咖啡|午饭|午休|书桌|台灯|日历|邮件|报错|日志|版本|分支|草稿|推文|稿子)"
)
_MEME = re.compile(
    r"(牛马|摸鱼|摆烂|连滚带爬|火葬场|狠人|卷王|躺平)"
)
# 整句关怀腔：没有事实半句可留，截前半句仍是安慰——整句删，不截断
_COMFORT_WHOLE = re.compile(
    r"(你已经(很|够|超|挺|那么|这么)|你值得|好好(爱|善待|心疼)?自己"
    r"|照顾好?自己|爱惜自己|犒劳(好)?自己|不要给自己.{0,4}(压力|负担)"
    r"|(会一直|一直|永远)陪(着|在)?你|一切都会(好|过去))"
)
# 空铺垫半句：截出来没有信息量（"无论结果如何。""忙碌的日子里。"）
_EMPTY_HEAD = re.compile(
    r"^(无论|不管).{0,8}(如何|怎样|与否)$"
    r"|^.{0,6}的日子里$"
)

# 腔调 → (口味条目, 处理动作) 的映射；动作用于产出候选
_VOICE_RULES = {
    "T-VOICE-01": ("T1", "删掉逗号后的劝慰半句，只留前半段的事实"),
    "T-VOICE-02": ("T2", "删升华半句，保留动作"),
    "T-VOICE-03": ("T3", "整句删；设计理由移到设计文档"),
    "T-VOICE-04": ("T4", "整句删；有收益就换成数字+条件"),
    "T-VOICE-05": ("T5", "压到最短，或删"),
    "T-VOICE-06": ("T6", "改成吐槽或自嘲，别捧读者"),
    "T-VOICE-07": ("T7", "砍掉「X的你」框架，直接用第二人称说动作"),
    "T-VOICE-08": ("T8", "降级到具体名词：谁、在哪、什么事"),
    "T-VOICE-09": ("T9", "删；要玩这个梗就把矛头指向自己"),
    "T-VOICE-10": ("T10", "删；鼓励要有具体对象"),
    "T-VOICE-11": ("T12", "删收束词，直接说结论"),
    "T-MANUAL-01": ("T11", "整句删（本人口径：功能说明不压缩、不改写）"),
    "T-MANUAL-02": ("T11", "整句删；必须保留时压到最短指令"),
    "T-MANUAL-03": ("T11", "删标题，界面本身即说明"),
}


@dataclass
class LineAdvice:
    text: str
    action: str  # 删 / 改 / 保留
    taste: list[str] = field(default_factory=list)  # 命中口味条目编号
    rules: list[str] = field(default_factory=list)  # 命中规则 id
    reason: str = ""
    candidate: str = ""  # 规则化改写候选（可能为空）
    direction: str = ""  # 方向提示（缺梗时可读）

    def to_dict(self) -> dict:
        return {
            "text": self.text, "action": self.action, "taste": self.taste,
            "rules": self.rules, "reason": self.reason,
            "candidate": self.candidate, "direction": self.direction,
        }

    def proposal(self) -> str:
        """改写器对这条的"产出物"——回归评测按它算风格维度。

        删 → 空串（删是最短形态）；改 → 候选文本，无候选则用方向提示；
        保留 → 原文本身（改写器的提案就是"照用"）。
        """
        if self.action == DELETE:
            return ""
        if self.action == REWRITE:
            return self.candidate or self.direction
        return self.text


@dataclass
class RewriteResult:
    advices: list[LineAdvice] = field(default_factory=list)
    profile: str = "personal"

    @property
    def findings(self):
        """把"删/改"档暴露成 Finding 形状，便于回归评测与统一渲染。"""
        out = []
        for a in self.advices:
            if a.action == KEEP:
                continue
            out.append(engine.Finding(
                rule_id=a.rules[0] if a.rules else "T-MANUAL-01",
                rule_name="口味命中", severity="medium", tier="syntactic", para=0,
                sentence=a.text, matches=a.taste,
                explanation=a.reason, suggestion=a.candidate or a.direction,
                taste=a.taste[0] if a.taste else "",
            ))
        return out

    @property
    def hints(self):
        return []


def _strip_trailing_paren(text: str) -> str:
    return re.sub(r"[（(][^）)]*[）)]\s*$", "", text).strip()


def _mechanical_candidate(text: str, rule_ids: list[str]) -> tuple[str, str, str]:
    """规则化改写：只做能安全机械执行的操作，其余给方向提示。

    返回 (候选, 方向, 驱动规则 id)——候选与方向最多一个非空；驱动规则用于
    让报告里的"理由"和实际建议对上（不能建议删、却解释升华腔）。
    """
    body = _strip_trailing_paren(text.rstrip("。！？～")).strip()
    heads = [h.strip() for h in re.split(r"[，,]", body) if h.strip()]

    # 功能说明腔：本人口径是整句删，不产出候选
    driver = next((i for i in rule_ids if i.startswith("T-MANUAL")), "")
    if driver:
        return "", "整句删（本人口径：功能说明不保留、不压缩）", driver

    # 劝诫长句：铺垫+祈使的结构删掉，关心不必写成劝诫
    if "T-VOICE-05" in rule_ids:
        return "", "整句删；要表达关心就一句话，别加理由", "T-VOICE-05"

    # 劝慰腔：先看整句是不是纯关怀（赞美/爱自己/陪伴/照顾自己）——
    # 是则整句删；否则砍掉逗号后的安慰半句，只留前半段事实
    if "T-VOICE-01" in rule_ids:
        if _COMFORT_WHOLE.search(body):
            return "", "整句删；纯关怀没有事实可留", "T-VOICE-01"
        if len(heads) >= 2 and not _EMPTY_HEAD.search(heads[0]):
            return heads[0] + "。", "", "T-VOICE-01"
        if len(heads) >= 2:
            return "", "整句删；前半句是空铺垫", "T-VOICE-01"

    # 抒情升华：升华句是逗号后的那半句，删掉它
    if "T-VOICE-02" in rule_ids and len(heads) >= 2:
        return heads[0] + "。", "", "T-VOICE-02"

    # 「X的你」句式：拆掉框架，保留后面的动作
    if "T-VOICE-07" in rule_ids:
        m = re.match(r"^[^，,]{0,10}的你[，,]\s*(.+)$", body)
        if m:
            return m.group(1).strip() + "。", "", "T-VOICE-07"

    # 收束词：删掉词直接说结论
    if "T-VOICE-11" in rule_ids:
        return re.sub(r"^(综上所述|总而言之|总的来说|由此可见|不得不说)[，,]?\s*", "", body) + "。", "", "T-VOICE-11"

    for rid in ("T-VOICE-10", "T-VOICE-12", "T-VOICE-04"):
        if rid in rule_ids:
            return "", "整句删（口号/顺口溜/效率承诺没有信息量）", rid

    # 其余腔调：给方向，不硬造候选
    hint = "删掉腔调半句，只留事实"
    if not _NOUN.search(body) and not _MEME.search(body):
        hint += "；再补一个具体名词或梗来承载信息"
    return "", hint, rule_ids[0] if rule_ids else ""


def classify_line(text: str, rules: list[engine.Rule] | None = None) -> LineAdvice:
    """单句判定：R1（重要内容优先）→ 腔调规则 → 三档输出。"""
    rules = rules if rules is not None else engine.load_rules("personal")
    line = text.strip()
    if not line:
        return LineAdvice(text=text, action=KEEP)

    hits: list[engine.Rule] = []
    for r in rules:
        if r.scope != "sentence":
            continue
        for p in r.patterns:
            if p.search(line):
                hits.append(r)
                break

    # R1 最高优先：含数字/结论的句子进保留档，腔调不覆盖
    if _IMPORTANT.search(line):
        taste = [r.taste for r in hits if r.taste]
        return LineAdvice(
            text=line, action=KEEP, taste=taste, rules=[r.id for r in hits],
            reason="含数据/结论，保留",
        )

    if not hits:
        # R3 只作提示不作判据：缺具体名词/梗是"没加分"，不是"有毛病"——
        # 拿它单独定罪会把功能标签、参数说明、help 文本全判成该改（自检实证
        # 47/53 命中），正是本项目的设计原则要避免的误判机器。保留档本身不
        # 需要理由，只在可能是文案时给一条轻提示。
        if _NOUN.search(line) or _MEME.search(line):
            return LineAdvice(text=line, action=KEEP)
        return LineAdvice(text=line, action=KEEP,
                          direction="若是文案，可补一个具体名词或梗")

    ids = [r.id for r in hits]
    tastes = [r.taste for r in hits if r.taste]
    cand, direction, driver = _mechanical_candidate(line, ids)

    # 判档：功能说明腔一律删（本人口径）；机械结果明示整句删也进删档；
    # 有候选就改；其余长句给方向让人改，短腔调整句删
    manual = any(i.startswith("T-MANUAL") for i in ids)
    if manual or (not cand and direction.startswith("整句删")):
        action = DELETE
    elif cand:
        action = REWRITE
    else:
        has_substance = len(_strip_trailing_paren(line)) > 14
        action = REWRITE if has_substance else DELETE

    # 理由取驱动本次建议的那条规则；没有驱动规则时退回最高严重级
    top = next((r for r in hits if r.id == driver), None) or min(
        hits, key=lambda r: engine.SEVERITY_ORDER.get(r.severity, 0) * -1)
    return LineAdvice(
        text=line, action=action, taste=tastes, rules=ids,
        reason=top.explanation.strip().split("。")[0] + "。",
        candidate=cand, direction=direction,
    )


_LEAD_MARKER = re.compile(r"^\s*(?:[-*+]\s+|\d+[.、)](?=\s|\D))\s*")


def rewrite_text(text: str, profile: str = "personal") -> RewriteResult:
    """逐行给建议。文案池的惯例是一行一条——按行判定与 JS 端同构，
    避免两端在"一行多句"上出现口径漂移（一致性探针会抓）。
    行首的列表符（- / * / 1. / 1、）剥掉再判，避免锚定模式漏匹配。"""
    rules = engine.load_rules(profile)
    result = RewriteResult(profile=profile)
    for raw in text.splitlines():
        line = _LEAD_MARKER.sub("", raw.strip())
        if not line:
            continue
        result.advices.append(classify_line(line, rules))
    return result


def render_advice(result: RewriteResult) -> str:
    """终端/Markdown 通用文本渲染。"""
    icon = {DELETE: "删", REWRITE: "改", KEEP: "留"}
    keep = [a for a in result.advices if a.action == KEEP]
    out = [f"改写建议（{result.profile}）：共 {len(result.advices)} 条 · "
           f"删 {sum(1 for a in result.advices if a.action == DELETE)} · "
           f"改 {sum(1 for a in result.advices if a.action == REWRITE)} · 保留 {len(keep)}", ""]
    for a in result.advices:
        tag = icon.get(a.action, "?")
        taste = f" {'/'.join(a.taste)}" if a.taste else ""
        out.append(f"[{tag}]{taste} {a.text}")
        if a.reason:
            out.append(f"      {a.reason}")
        if a.candidate:
            out.append(f"   → {a.candidate}")
        elif a.direction:
            out.append(f"   → {a.direction}")
        out.append("")
    out.append("改写准则：重要数据和结论要保留；梗得人来补——只给规则化建议，不替你造梗。")
    return "\n".join(out)
