"""边界情况：不崩溃、合理降级。"""
import math

from human_vs_ai import engine


class TestEdges:
    def test_empty(self):
        r = engine.analyze("", "academic")
        assert r.doc_stats.n_sentences == 0
        assert not r.findings

    def test_whitespace_only(self):
        r = engine.analyze("   \n\n   \t  ", "academic")
        assert r.doc_stats.n_sentences == 0

    def test_codeblock_only(self):
        r = engine.analyze("```python\nprint('hello')\n```", "academic")
        assert r.doc_stats.n_sentences == 0

    def test_no_punctuation_long_line(self):
        # 无句末标点的超长行：整行算一句，统计不炸
        r = engine.analyze("字" * 5000, "academic")
        assert r.doc_stats.n_sentences == 1
        assert r.doc_stats.n_chars == 5000

    def test_english_text(self):
        # 英文切分规则不同（句号无空格判定），至少不崩溃
        r = engine.analyze("This is a test. It has sentences. It works!", "academic")
        assert r.doc_stats.n_sentences >= 1

    def test_single_short_sentence(self):
        r = engine.analyze("这是一句话。", "academic")
        # 样本不足的统计指标为 NaN，序列化时为 null——不判、不炸
        assert math.isnan(r.doc_stats.sentence_cv)

    def test_crlf_and_bom(self):
        r = engine.analyze("第一段第一句。\r\n\r\n第二段第一句。\r\n", "academic")
        assert r.doc_stats.n_paragraphs == 2

    def test_repeated_heading_lines(self):
        text = "# 论文\n## 1 引言\n\n随着深度学习的发展，方法越来越多。\n\n## 2 方法\n\n正文在这里。这里有数据：准确率 91.2%。\n"
        r = engine.analyze(text, "academic")
        # 标题行剥离后只剩正文两段
        assert r.doc_stats.n_paragraphs == 2
        assert any(f.rule_id == "L-FORM-01" for f in r.findings + r.hints)

    def test_billion_laughs_style_repetition(self):
        # 极端重复不炸不慢
        text = "综上所述，该方法具有重要意义。\n" * 2000
        r = engine.analyze(text, "academic")
        assert len(r.findings) > 100
