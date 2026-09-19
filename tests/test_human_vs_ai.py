"""单元测试：切分、统计、引擎、端到端区分度冒烟。

运行：python -m pytest tests/ -q
"""
from pathlib import Path

import pytest

from human_vs_ai import engine, segment, stats

DATA = Path(__file__).parent / "data"
AI_TEXT = (DATA / "ai_academic.txt").read_text(encoding="utf-8")
HUMAN_TEXT = (DATA / "human_academic.txt").read_text(encoding="utf-8")


# ---------- segment ----------

class TestSegment:
    def test_basic_split(self):
        sents = segment.split_sentences("第一句。第二句！第三句？")
        assert [s.text for s in sents] == ["第一句。", "第二句！", "第三句？"]

    def test_quote_inner_punct_not_boundary(self):
        sents = segment.split_sentences('他说"这一点很关键。"然后走了。')
        assert len(sents) == 1
        assert "关键" in sents[0].text

    def test_ellipsis_boundary(self):
        sents = segment.split_sentences("先这样……然后呢。")
        assert len(sents) == 2

    def test_markdown_structure_dropped(self):
        text = "# 标题\n\n正文第一段。\n\n```python\ncode = 1\n```\n\n| a | b |\n|---|---|\n\n正文第二段。"
        paras = segment.split_paragraphs(segment.strip_markdown(text))
        assert len(paras) == 2
        assert all("```" not in p and "|" not in p for p in paras)

    def test_empty_and_short(self):
        assert segment.split_sentences("") == []
        assert segment.split_sentences("无标点结尾") != []


# ---------- stats ----------

class TestStats:
    def test_cv_uniform_vs_varied(self):
        uniform = [10.0, 10.0, 10.0, 10.0, 10.0]
        varied = [3.0, 40.0, 5.0, 38.0, 4.0]
        assert stats._cv(varied) > stats._cv(uniform)
        assert stats._cv(uniform) == pytest.approx(0.0)

    def test_cv_too_few_samples(self):
        import math
        assert math.isnan(stats._cv([1.0, 2.0]))

    def test_mattr_length_invariant(self):
        # 同一分布重复采样，长短文本 MATTR 应接近（全文 TTR 做不到）
        tokens = (["词"] * 20 + [f"字{i}" for i in range(30)]) * 10
        short = stats.mattr(tokens[:200])
        long = stats.mattr(tokens)
        assert abs(short - long) < 0.15

    def test_four_gram_repeat(self):
        assert stats._four_gram_repeat("提供了新的思路提供了新的思路提供了新的思路") > 0.5
        assert stats._four_gram_repeat("一二三四五六七八九十甲乙丙丁戊己庚辛") == 0.0


# ---------- engine ----------

class TestEngine:
    def test_profiles_available(self):
        profiles = engine.available_profiles()
        assert "academic" in profiles and "general" in profiles

    def test_load_rules(self):
        rules = engine.load_rules("academic")
        assert len(rules) >= 15
        ids = {r.id for r in rules}
        assert "L-INFL-01" in ids and "D-UNIF-01" in ids

    def test_general_profile_loads(self):
        rules = engine.load_rules("general")
        ids = {r.id for r in rules}
        # 三连排比从 academic 证伪后移入 general 复活
        assert "G-TRIAD-01" in ids and "S-TRIAD-01" not in ids
        # 问答文体连接词密度反向（真人医疗模板更高），D-CONN 不进 general
        assert "D-CONN-01" not in ids

    def test_general_hits(self):
        r = engine.analyze("好的，以下是关于时间管理的一些建议。\n\n希望对你有所帮助！", "general")
        ids = {f.rule_id for f in r.findings + r.hints}
        assert "G-OPEN-01" in ids
        assert "G-INTERACT-01" in ids

    def test_bad_profile(self):
        with pytest.raises(FileNotFoundError):
            engine.load_rules("nonexistent")

    def test_word_rule_hits(self):
        result = engine.analyze("这项工作标志着该领域的重要突破。", "academic")
        rule_ids = {f.rule_id for f in result.findings}
        assert "L-INFL-01" in rule_ids

    def test_low_rule_single_hit_becomes_hint(self):
        # 只有一个"不仅…更是"→ 共现不足，进 hints 不进 findings
        text = "该材料不仅性能好，更是成本低。\n\n第二段讲的是另一件事，篇幅足够长，不是为了凑共现的判定条件而存在，这里需要有足够的内容让分析正常进行下去。\n\n第三段继续补充足够的内容，使得整个文本不至于因为太短而在统计指标上失效，从而单独考察弱规则的降级行为。"
        result = engine.analyze(text, "academic")
        assert any(f.rule_id == "L-NEGA-01" for f in result.hints)
        assert not any(f.rule_id == "L-NEGA-01" for f in result.findings)

    def test_low_rule_cooccurrence_upgrades(self):
        text = "该材料不仅性能好，更是成本低。这不是简单的改进，而是原理层面的革新。\n\n第二段需要足够长，以便让全文统计指标处于正常区间，不至于因为文本过短而触发别的规则，从而单独验证共现升级逻辑是否正确工作。这里再补一些实质性的内容描述。"
        result = engine.analyze(text, "academic")
        assert any(f.rule_id == "L-NEGA-01" for f in result.findings)

    def test_one_liner_shape(self):
        text = "这是一段很长的正文内容，讨论了实验的设计、过程与结果，包含足够多的细节描写，目的是让它明显超过独句段的长度阈值，从而与最后那段独句总结形成对照。\n\n综上所述，该方法可行。"
        result = engine.analyze(text, "academic")
        hits = [f for f in result.findings + result.hints if f.rule_id == "S-CLOS-01"]
        assert hits

    def test_nan_safe_on_tiny_input(self):
        result = engine.analyze("很短。", "academic")
        assert isinstance(result.doc_stats.n_sentences, int)


# ---------- 端到端区分度冒烟 ----------

class TestDiscrimination:
    def test_ai_hits_more_than_human(self):
        ai = engine.analyze(AI_TEXT, "academic")
        human = engine.analyze(HUMAN_TEXT, "academic")
        assert len(ai.findings) > len(human.findings) * 2, (
            f"AI 命中 {len(ai.findings)} vs 人类 {len(human.findings)}"
        )

    def test_human_cv_higher(self):
        ai = engine.analyze(AI_TEXT, "academic")
        human = engine.analyze(HUMAN_TEXT, "academic")
        assert human.doc_stats.sentence_cv > ai.doc_stats.sentence_cv

    def test_ai_text_triggers_inflation(self):
        ai = engine.analyze(AI_TEXT, "academic")
        rule_ids = {f.rule_id for f in ai.findings + ai.hints}
        assert "L-INFL-01" in rule_ids
        assert "L-FORM-01" in rule_ids
