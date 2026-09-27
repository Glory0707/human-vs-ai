"""误判回归语料：把真实误判类型固化成合成样例钉住，防复活。

来源：v0.30.0 误判全量清点（52 篇高分误报）——主犯 G-CLOS-01 独句总结段
在知乎域反向（真人 95% vs AI 36%），撤出后真人尾部 ≥60 误报 52 → 24。
入库样例一律合成（真实语料不进仓库，判别式照 docs/rules.md 自造）。
运行：python -m pytest tests/test_misjudge.py -q
"""
from human_vs_ai import engine

# 知乎式口语排版：逐段一句、对读者喊话、设问自答——曾是 G-CLOS-01
# 的重灾区（100 分/52 处发现）
COLLOQUIAL = (
    "啥叫内卷？\n\n"
    "就是所有人都累，但谁也不敢停。\n\n"
    "为什么不敢停？\n\n"
    "因为蛋糕就那么大，你慢一步就被挤下去。\n\n"
    "所以别问我怎么办，我先躺了。\n\n"
    "你们随意。\n\n"
    "反正我是想明白了，人这一辈子，健康开心最重要。\n\n"
    "其它的，爱咋咋地吧。"
)


def test_one_liner_rule_retired_from_general():
    ids = {r.id for r in engine.load_rules("general")}
    assert "G-CLOS-01" not in ids, "独句总结段在知乎域反向（真人 95% vs AI 36%），不得复活"


def test_colloquial_layout_no_one_liner_hits():
    r = engine.analyze(COLLOQUIAL, "general")
    assert "G-CLOS-01" not in {f.rule_id for f in r.findings + r.hints}


def test_colloquial_findings_stay_bounded():
    # 规则层撤出后，口语排版的词表发现量级应从几十处回落到个位数
    r = engine.analyze(COLLOQUIAL, "general")
    assert len(r.findings) <= 6, f"发现 {len(r.findings)} 处——口语排版误伤回潮？"


class TestDegenerateInput:
    """极端输入（v0.30.3）：纯标点碎片曾实测 official 轰出 100 分/40 处
    发现——无内容句不匹配规则、全文无有效字符抑制出分。"""

    PUNCT = "。，！？" * 40

    def test_pure_punct_no_findings(self):
        for prof in ("general", "official", "essay", "academic"):
            r = engine.analyze(self.PUNCT, prof)
            assert not r.findings and not r.hints, f"{prof}: {len(r.findings)} 处"

    def test_pure_punct_suppressed(self):
        for prof in ("general", "official"):
            r = engine.analyze(self.PUNCT, prof)
            assert r.score is None

    def test_suppress_note_precedence(self):
        # 无评分 profile：具体原因（无有效文本）不被通用"该文体未校准"覆盖
        r = engine.analyze(self.PUNCT, "personal")
        assert r.scoring_note == "无有效文本"

    def test_content_sentence_still_flagged(self):
        # 守卫不得误伤正常句子：感叹号规则照常工作
        r = engine.analyze("让我们凝心聚力、真抓实干，为建设美丽家园而努力奋斗！"
                           "第二句正常表述。第三句也没问题。第四句完整。第五句收束。",
                           "official")
        assert any(f.rule_id == "O-EXCL-01" for f in r.findings)
