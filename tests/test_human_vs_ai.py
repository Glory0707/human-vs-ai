"""单元测试：切分、统计、引擎、端到端区分度冒烟。

运行：python -m pytest tests/ -q
"""
from pathlib import Path

import pytest

from human_vs_ai import engine, report, segment, stats

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

    def test_ascii_single_quote_not_a_quote(self):
        # 英文所有格/缩写的撇号不参与引号切换（首轮审计发现的切分 bug）
        sents = segment.split_sentences("It's fine。Next sentence。")
        assert [s.text for s in sents] == ["It's fine。", "Next sentence。"]

    def test_ellipsis_boundary(self):
        sents = segment.split_sentences("先这样……然后呢。")
        assert len(sents) == 2

    def test_markdown_structure_dropped(self):
        # 标题/代码块整块丢弃；表格分隔行丢弃，表头内容保留（新口径：
        # 列表/表格正文参与分析，v0.8.0 修订）
        text = "# 标题\n\n正文第一段。\n\n```python\ncode = 1\n```\n\n| 指标 | 数值 |\n|---|---|\n\n正文第二段。"
        joined = segment.strip_markdown(text)
        assert "```" not in joined and "标题" not in joined
        assert "正文第一段。" in joined and "正文第二段。" in joined
        assert "指标" in joined and "数值" in joined
        assert "---" not in joined

    def test_list_items_kept_as_content(self):
        # 问答/自媒体用列表写正文——条目必须进分析，不能整块丢
        text = "- 首先要明确目标。\n- 其次要持续投入。\n* 综上所述，坚持才有回报。"
        doc = segment.split_document(text)
        kinds = [b.kind for b in doc]
        assert kinds == ["list"]
        sents = [s.text for s in doc[0].sents]
        assert len(sents) == 3
        assert sents[0] == "首先要明确目标。"

    def test_numbered_list_and_emphasis_kept(self):
        doc = segment.split_document("1. 首先进行预处理。\n2. 其次进行训练。")
        assert [s.text for b in doc for s in b.sents] == ["首先进行预处理。", "其次进行训练。"]
        # 中文星号强调剥离标记保留文字；算式 3*5 不被误剥
        doc2 = segment.split_document("*重点*在于效率，3*5 也算。")
        assert doc2[0].sents[0].text == "重点在于效率，3*5 也算。"

    def test_bare_url_stripped(self):
        text = "参考链接 https://example.com/a?x=1 显著提升了性能。"
        doc = segment.split_document(text)
        body = "".join(s.text for b in doc for s in b.sents)
        assert "example.com" not in body
        assert "显著提升了性能。" in body

    def test_email_strip_ascii_only(self):
        # 邮箱显式 ASCII 类：两端（Python \w 认 CJK，JS 不认）行为一致；
        # CJK"伪邮箱"是正常中文，不能误剥
        line = segment._inline_clean("联系 zhouao@example.com 结束。")
        assert "zhouao@example.com" not in line and "联系" in line and "结束" in line
        kept = segment._inline_clean("长@长.cn 不是邮箱。")
        assert kept == "长@长.cn 不是邮箱。"

    def test_emph_span_cap(self):
        # 强调内容上限 49+CJK+49 字：真实强调远小于此；上限是防
        # "未闭合星号+长文" O(n²) 灾难性回溯的代价（实测 4 万字 51 秒）
        assert segment._inline_clean("*强调*正文。") == "强调正文。"
        assert segment._inline_clean("**粗体内容**正文。") == "粗体内容正文。"
        long_span = "*" + "长" * 120 + "*"
        assert segment._inline_clean(long_span + "正文。") == long_span + "正文。"

    def test_link_strip(self):
        assert segment._inline_clean("[文字](https://x.com/a)混排。") == "文字混排。"

    def test_sentence_absorbs_closing_quote(self):
        # 边界标点后的闭引号并入本句（对拍实证 JS 曾漏吸收 → 句长分布漂移）
        sents = segment.split_sentences("结论如此。”下一句话。最后再确认一次无误。")
        assert sents[0].text == "结论如此。”"
        assert sents[1].text == "下一句话。"

    def test_unicode_line_breaks_join_with_newline(self):
        # Python splitlines 的行界全集（\u2028/\u2029/\v/\f/\x85/\x1c-\x1e/裸\r）
        # 都拆行；段内硬换行用 \n 重新拼接（JS 端 LINE_BREAK_RE 逐字符对齐）
        for br in ("\u2028", "\u2029", "\x0b", "\x0c", "\x85", "\x1c", "\x1d", "\x1e", "\r"):
            blocks = segment.split_document(f"段落一{br}段落二完整。")
            assert len(blocks) == 1, f"{br!r} 拆成了 {len(blocks)} 块"
            assert blocks[0].sents[0].text == "段落一\n段落二完整。"

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

    def test_mattr_rolling_equals_naive(self):
        # 滚动窗口是 O(n) 优化：distinct 计数必须与"逐窗建 set"逐位一致
        import random
        rng = random.Random(42)
        vocab = [f"词{i}" for i in range(25)]
        for n in (99, 100, 101, 500, 1237):
            tokens = [rng.choice(vocab) for _ in range(n)]
            window = 100
            if len(tokens) <= window:
                assert stats.mattr(tokens) == len(set(tokens)) / len(tokens)
                continue
            naive = sum(
                len(set(tokens[i : i + window])) / window
                for i in range(len(tokens) - window + 1)
            ) / (len(tokens) - window + 1)
            assert stats.mattr(tokens) == pytest.approx(naive)

    def test_ttr_unified_2gram(self):
        # v0.11.0 起 TTR 只用字级 2-gram 口径：与评分、与 JS 引擎逐位一致，
        # 装没装任何分词库数字都一样——这条钉住口径，防止词级切分悄悄回来
        paras = [["随着技术的快速发展，指标显著提升。", "其次，方法稳定收敛。"]]
        st = stats.compute_doc_stats(paras)
        full = "".join(s for p in paras for s in p)
        assert st.ttr == stats.mattr(stats.tokenize_2gram(full))
        assert st.to_dict()["tokenizer"] == "char-2gram"

    def test_avg_sentence_len_nan_when_no_sentences(self):
        # 空文本的"平均句长"是 NaN 不是 0——与 JS 端 mean([]) = NaN 同口径
        # （对拍实证漂移：Python 曾给 0.0，JSON 里一个是 0 一个是 null）
        st = stats.compute_doc_stats([[]])
        assert st.avg_sentence_len != st.avg_sentence_len
        assert st.to_dict()["avg_sentence_len"] is None


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


# ---------- 报告与规则文案 ----------

class TestProseQuality:
    def test_folded_yaml_no_cjk_gap(self):
        # YAML folded 块把换行折成空格——中文之间、中文标点两侧的空格都是
        # 伪影（"。 2023"、"—— “"），加载时必须清掉；普通汉字与英文单词
        # 之间的排版空格保留
        import re
        cjk = r"[一-鿿　-ヿ＀-￯]"
        punct = r"[　-〿＀-￯—‘’“”]"
        pat = re.compile(
            rf"(?<={cjk}) +(?={cjk})"
            rf"|(?<={punct}) +| +(?={punct})"
        )
        for profile in engine.available_profiles():
            for r in engine.load_rules(profile):
                for field in ("name", "explanation", "suggestion"):
                    v = getattr(r, field)
                    assert not pat.search(v), f"{profile}/{r.id}.{field}: {v[:50]!r}"

    def test_report_explains_each_rule_once(self):
        # 同一规则命中多句时，解释只出现一次（界面减法）
        text = "\n\n".join(
            f"第{i}段首先给出一个论点，其次展开论证，此外还补充了旁证，与此同时保持行文完整，"
            f"最后综上所述收束全段，让全文句数充足到统计判定可以正常进行，不至于触发短文本保护。"
            for i in range(4)
        )
        result = engine.analyze(text, "academic")
        hits = [f for f in result.findings if f.rule_id == "L-CONN-01"]
        assert len(hits) >= 2, "前置条件：L-CONN-01 多句命中"
        md = report.render_markdown(result)
        assert md.count("这批词本身没有错") == 1

    def test_group_title_dedupes_repeated_rules(self):
        # 同段大量相同句折叠成一组：标题不许把同一 ID 拼接几十遍
        # （浏览器实测：1200 个相同句曾生成整版 "G-SAFE-01 + G-SAFE-01 + …"）
        result = engine.analyze("首先，做了这件事。" * 60, "academic")
        assert len(result.findings) >= 40, "前置条件：同段相同句大量命中"
        for out in (report.render_markdown(result), report.render_terminal(result)):
            assert "L-CONN-01 + L-CONN-01" not in out
            assert "模板连接词 + 模板连接词" not in out

    def test_dir_input_clean_error(self, tmp_path, capsys):
        # 目录当输入：干净报错，不抛裸堆栈
        from human_vs_ai import cli
        with pytest.raises(SystemExit) as ei:
            cli.main(["check", str(tmp_path)])
        assert "目录" in str(ei.value)

    def test_unknown_profile_clean_error(self, tmp_path):
        from human_vs_ai import cli
        f = tmp_path / "a.txt"
        f.write_text("正文。", encoding="utf-8")
        for argv in (["check", str(f), "-p", "nope"],
                     ["stats", str(f), "-p", "nope"],
                     ["explain", "L-INFL-01", "-p", "nope"],
                     ["rewrite", str(f), "-p", "nope"]):
            with pytest.raises(SystemExit) as ei:
                cli.main(argv)
            assert "不存在" in str(ei.value) and "nope" in str(ei.value)

    def test_stats_command_stats_only(self, tmp_path, capsys):
        # stats 契约：只出统计特征，不夹带 findings/hints/disclaimer
        import json as _json
        from human_vs_ai import cli
        f = tmp_path / "a.txt"
        f.write_text("随着人工智能的快速发展，方法越来越多。" * 6, encoding="utf-8")
        cli.main(["stats", str(f)])
        payload = _json.loads(capsys.readouterr().out)
        assert set(payload) == {"tool", "version", "profile", "stats"}
        assert "n_sentences" in payload["stats"]
        assert "findings" not in payload

    def test_check_and_stats_read_stdin(self, capsys, monkeypatch):
        # 管道输入：check/stats 的 "-" 与 rewrite 同口径
        import io
        import json as _json
        from human_vs_ai import cli
        text = "综上所述，系统显著提升了性能。" * 5
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        cli.main(["check", "-"])
        assert "human-vs-ai" in capsys.readouterr().out
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        cli.main(["stats", "-"])
        payload = _json.loads(capsys.readouterr().out)
        assert payload["stats"]["n_sentences"] >= 1

    def test_hints_capped_in_renders_not_json(self, capsys):
        # 弱命中超过 12 条只展示 12 条+略注；JSON 是事实源，永不截断
        import json as _json
        from human_vs_ai import stats as _stats
        hint = engine.Finding(
            rule_id="L-X-01", rule_name="弱规则", severity="low", tier="lexical",
            para=0, sentence="句", matches=["词"], explanation="解释", suggestion="",
        )
        result = engine.AnalysisResult(
            findings=[], hints=[hint] * 15,
            doc_stats=_stats.DocStats(n_paragraphs=1, n_sentences=10, n_chars=100),
            profile="academic",
        )
        term = report.render_terminal(result)
        md = report.render_markdown(result)
        for out in (term, md):
            assert "列前 12 处" in out and "15 处" in out
        assert term.count("  · L-X-01") == 12  # 列表体只列 12 条
        assert md.count("- L-X-01") == 12
        payload = _json.loads(report.render_json(result))
        assert len(payload["hints"]) == 15


# ---------- 端到端区分度冒烟 ----------

class TestDiscrimination:
    def test_ai_hits_more_than_human(self):
        ai = engine.analyze(AI_TEXT, "academic")
        human = engine.analyze(HUMAN_TEXT, "academic")
        assert len(ai.findings) > len(human.findings) * 2, (
            f"AI 命中 {len(ai.findings)} vs 人类 {len(human.findings)}"
        )

    def test_auroc_nan_safe(self):
        # NaN 分数（<3 句文本的 CV 走到打分器里）曾让并列检测死循环——
        # auroc 入口必须剔除非有限分数（服务器分档拟合实证挂起点）
        import math
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        from evaluate_cred import auroc
        score = auroc([float("nan"), 0.9, 0.8], [0.1, 0.2, float("nan")])
        assert not math.isnan(score) and 0 <= score <= 1

    def test_human_cv_higher(self):
        ai = engine.analyze(AI_TEXT, "academic")
        human = engine.analyze(HUMAN_TEXT, "academic")
        assert human.doc_stats.sentence_cv > ai.doc_stats.sentence_cv

    def test_ai_text_triggers_inflation(self):
        ai = engine.analyze(AI_TEXT, "academic")
        rule_ids = {f.rule_id for f in ai.findings + ai.hints}
        assert "L-INFL-01" in rule_ids
        assert "L-FORM-01" in rule_ids


# ---------- 综合评分（AI 味指数） ----------

class TestScore:
    def test_score_monotonic_on_fixtures(self):
        # 指数综合规则密度与全文统计：AI fixture 必须显著高于人类 fixture，
        # 且两者的差主要由规则贡献（fixture 的主要差异就是模板密度）
        ai = engine.analyze(AI_TEXT, "academic")
        human = engine.analyze(HUMAN_TEXT, "academic")
        assert ai.score is not None and human.score is not None
        assert ai.score.index > human.score.index
        assert ai.score.components["hit_density"] > human.score.components["hit_density"]

    def test_score_band_anchors_to_human_percentiles(self):
        # 分档锚点来自校准语料真人分位，跟着 YAML 走（校准只改数据）
        ai = engine.analyze(AI_TEXT, "academic")
        s = ai.score
        assert s.human_p50 > 0 and s.human_p90 > s.human_p50
        assert s.index > s.human_p90  # AI fixture 应落在高档
        assert 0 < s.auroc <= 1

    def test_score_short_text_none(self):
        # <8 句不出分：统计层不判，评分也不判
        assert engine.analyze("你好呀。", "academic").score is None

    def test_score_tolerates_null_fields(self):
        # 病态配置容错：YAML 里显式 null 的 intercept/系数/分位
        # 不该炸报告——null 系数当缺省跳过，null 分位落 0
        r = engine.analyze(AI_TEXT, "academic")
        scoring = {"intercept": None, "hit_density": None, "sentence_cv": None,
                   "ttr": 10.0, "ngram_repeat": None, "conn_density": None,
                   "corpus": "t", "auroc": None, "human_p50": None, "human_p90": None}
        s = engine.compute_score(r.doc_stats, r.findings, r.hints, scoring)
        assert s is not None
        assert list(s.components) == ["ttr"]
        assert s.human_p50 == 0 and s.human_p90 == 0
        assert s.auroc != s.auroc  # NaN
        assert 0.0 <= s.index <= 100.0

    def test_score_uncalibrated_profiles_none(self):
        # v0.17.8 起 official 获真人配对语料出分（71 真人 vs 70 AI 拟合）；
        # personal 是改写层仍不出分
        assert engine.load_scoring("official") is not None
        assert engine.load_scoring("personal") is None
        r = engine.analyze(AI_TEXT, "personal")
        assert r.score is None
        # 够 8 句却没分要给原因（不然用户从学术切过来纳闷分去哪了）；
        # 短文本保持空——短文本不展示统计行，多一行解释反而吵
        assert "未校准" in r.scoring_note
        short = engine.analyze("你好呀。今天天气不错。", "personal")
        assert short.scoring_note == ""

    def test_score_tier_override_by_length(self):
        # scoring.tiers：长文用专属系数（v0.12.0 分档拟合：长档 holdout
        # 0.975 vs 全局 0.946）；元字段（分位锚点/corpus）始终取全局
        scoring = {
            "intercept": 0.0, "hit_density": 1.0, "sentence_cv": 0.0,
            "ttr": 0.0, "ngram_repeat": 0.0,
            "corpus": "test", "auroc": 0.9, "human_p50": 10, "human_p90": 80,
            "tiers": [{"min_chars": 600, "intercept": 2.0, "hit_density": 5.0}],
        }
        from human_vs_ai import stats as _stats
        st = _stats.DocStats(n_sentences=20, n_chars=1000, sentence_cv=0.4,
                             ttr=0.9, ngram_repeat=0.1, conn_density=0.0)
        long_score = engine.compute_score(st, [], [], scoring)
        st.n_chars = 300
        short_score = engine.compute_score(st, [], [], scoring)
        assert long_score.index > short_score.index  # 长档 hit_density 权重更大且截距更高
        assert long_score.human_p50 == 10 and long_score.corpus == "test"  # 元字段取全局
        # 边界：min_chars 含端点（600 用长档，599 用全局）
        st.n_chars = 600
        assert engine.compute_score(st, [], [], scoring).index == long_score.index
        st.n_chars = 599
        assert engine.compute_score(st, [], [], scoring).index == short_score.index

    def test_score_note_in_renders_and_json(self):
        import json as _json
        r = engine.analyze(AI_TEXT, "personal")
        assert "该文体未校准" in report.render_terminal(r)
        payload = _json.loads(report.render_json(r))
        assert payload["score"] is None
        assert "未校准" in payload["score_note"]

    def test_score_in_renders_and_json(self):
        import json as _json
        r = engine.analyze(AI_TEXT, "academic")
        term = report.render_terminal(r)
        md = report.render_markdown(r)
        for out in (term, md):
            assert "AI 味指数" in out and "构成" in out
        payload = _json.loads(report.render_json(r))
        assert payload["score"]["index"] == round(r.score.index, 1)
        assert "hit_density" in payload["score"]["components"]
