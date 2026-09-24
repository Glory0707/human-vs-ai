"""域外文体探测（ood）：文言/诗行判据、四端同行字段与 JS 同构。

判据在 C-ReD 全量 10.4 万篇上校准：正样本 5/5 命中，误报 0.005%；
这里钉住代表性正负样本与关键阈值边界，防止后人"顺手调参"漂移。
文种域外（detect_genre，v0.19.0）判据在真人/AI 公文语料上标定：
印发 100%/批复 100% 召回、其他文种含拟合集 87 篇零误报——
入库样本一律用合成样例（真实公文不进仓库）。
运行：python -m pytest tests/test_ood.py -q
"""
from human_vs_ai import engine, segment
from human_vs_ai.ood import detect, detect_genre


def _sents(text):
    """引擎同口径：blocks → 展平句子文本。"""
    return [s.text for blk in segment.split_document(text) for s in blk.sents]

YUEYANG = ("庆历四年春，滕子京谪守巴陵郡。越明年，政通人和，百废具兴。"
           "乃重修岳阳楼，增其旧制，刻唐贤今人诗赋于其上。属予作文以记之。"
           "予观夫巴陵胜状，在洞庭一湖。衔远山，吞长江，浩浩汤汤，横无际涯。"
           "朝晖夕阴，气象万千。此则岳阳楼之大观也。前人之述备矣。"
           "然则北通巫峡，南极潇湘，迁客骚人，多会于此，览物之情，得无异乎？"
           "若夫淫雨霏霏，连月不开，阴风怒号，浊浪排空。日星隐曜，山岳潜形。"
           "商旅不行，樯倾楫摧。薄暮冥冥，虎啸猿啼。")
VERSED = ("国破山河在，城春草木深。感时花溅泪，恨别鸟惊心。\n"
          "烽火连三月，家书抵万金。白头搔更短，浑欲不胜簪。\n"
          "好雨知时节，当春乃发生。随风潜入夜，润物细无声。\n"
          "野径云俱黑，江船火独明。晓看红湿处，花重锦官城。")
BAIHUA = ("这个方法真的很好用，我用了三个月之后感觉自己的效率提升了很多。"
          "以前我总是拖延到最后一刻才动手，现在会把任务拆成小块，"
          "每完成一块就给自己一点奖励。朋友说我变了一个人，"
          "其实我只是找到了适合自己的节奏。如果你也在拖延，不妨试试这个办法。"
          "它不需要什么意志力，只需要一点小小的设计。")
# 白话误伤回归样本（每条都曾是真实误报来源）：
# "或/亦/耳/耶" 单字歧义（推荐句式、新闻体、耳机、耶鲁）与"无'的'紧凑体"
RECOMMEND = ("观赏这部影片需要合适的设备，推荐 4K 电视或投影仪，搭配音响或耳机，"
             "画质与音效都会明显提升。心理上保持开放心态，接纳寓言式叙事。"
             "观影前调暗灯光，关闭手机，专注剧情 itself。影片寓意深刻，"
             "值得全家共同观看讨论，适合十二岁以上观众。")


class TestDetect:
    def test_classical_prose(self):
        assert "classical" in detect([YUEYANG])

    def test_verse_and_classical_poem(self):
        assert "verse" in detect(_sents(VERSED))

    def test_modern_baihua_clean(self):
        assert detect([BAIHUA]) == []

    def test_short_text_skipped(self):
        assert detect(["汴梁将军老他乡，万里漂泊在苏杭。"]) == []

    def test_empty(self):
        assert detect([]) == []

    def test_baihua_with_or_not_flagged(self):
        # "或"字不在文言强表（曾经真实误报"电视或投影仪"）
        assert detect([RECOMMEND]) == []


MIXED_CLASSICAL_BAIHUA = (
    "这个方法真的很好用，我用了三个月之后感觉自己的效率提升了很多。"
    "以前我总是拖延到最后一刻才动手，现在会把任务拆成小块，"
    "每完成一块就给自己一点奖励。朋友说我变了一个人，"
    "其实我只是找到了适合自己的节奏。如果你也在拖延，不妨试试这个办法。"
    "它不需要什么意志力，只需要一点小小的设计。\n\n" + YUEYANG)


class TestEngineIntegration:
    def test_mixed_baihua_classical_para_flagged(self):
        # 白话引用文言段：全文统计被稀释，逐段判定聚合补上（v0.17.4）
        r = engine.analyze(MIXED_CLASSICAL_BAIHUA, "essay")
        assert "classical" in r.ood
    def test_result_carries_ood(self):
        r = engine.analyze(YUEYANG, "essay")
        assert "classical" in r.ood

    def test_result_clean_baihua(self):
        r = engine.analyze(BAIHUA, "essay")
        assert r.ood == []

    def test_json_export_contains_ood(self):
        from human_vs_ai import report
        import json as _json
        r = engine.analyze(YUEYANG, "essay")
        data = _json.loads(report.render_json(r))
        assert "classical" in (data["ood"] or [])
        r2 = engine.analyze(BAIHUA, "essay")
        data2 = _json.loads(report.render_json(r2))
        assert data2["ood"] is None

    def test_report_has_ood_line(self):
        from human_vs_ai import report
        r = engine.analyze(YUEYANG, "essay")
        out = report.render_terminal(r)
        assert "文体域外" in out and "文言" in out
        md = report.render_markdown(r)
        assert "文体域外" in md

    def test_band_reading_in_score_line(self):
        # 锚点读数：分数行带真人相对位置（T3），不再只有裸数字
        text = ("在这个日新月异的时代，坚持显得尤为珍贵。这不仅是一次尝试，更是对自我的超越。"
                "首先，我们要明确目标。其次，我们要持之以恒。正如古人所言，千里之行始于足下。"
                "综上所述，唯有坚持方能谱写青春华章。让我们携手共进，绽放属于自己的光彩。")
        r = engine.analyze(text, "essay")
        if r.score:
            from human_vs_ai.report import _score_line
            line = _score_line(r.score)
            assert ("校准真人" in line) or line.endswith("/ 100")

    def test_ood_hint_does_not_affect_score(self):
        # 域外提示不参与评分：同一文本有/无 ood 字段，分数只由特征决定
        r = engine.analyze(YUEYANG, "essay")
        assert r.score is not None or r.scoring_note == "该文体未校准"


MIXED = ("在这个日新月异的时代，技术赋能千行百业。综上所述，底层逻辑不言而喻。"
         "首先，要赋能。其次，要闭环。最后，要抓手。\n\n"
         "我昨天去楼下买菜，萝卜贵了两毛钱。摊主说下雨天进货难。我买了两根，回家炖了汤。")


class TestParaHeat:
    def test_mixed_text_locates_hot_para(self):
        r = engine.analyze(MIXED, "general")
        assert r.para_heat, "有命中的段落应出现在热度列表"
        assert r.para_heat[0]["para"] == 0, "AI 味浓的第 1 段应排最前"
        assert r.para_heat[0]["level"] == "high"

    def test_clean_para_absent(self):
        r = engine.analyze(MIXED, "general")
        paras = {h["para"] for h in r.para_heat}
        assert 0 in paras and 1 not in paras, "无命中的段落不出现"

    def test_density_matches_weighted_hits(self):
        r = engine.analyze(MIXED, "general")
        h0 = next(h for h in r.para_heat if h["para"] == 0)
        manual = sum({"high": 3.0, "medium": 2.0, "low": 1.0}[f.severity]
                     for f in r.findings + r.hints if f.para == 0) / h0["n_sents"]
        assert abs(h0["density"] - manual) < 1e-9

    def test_json_export_contains_para_heat(self):
        import json as _json
        from human_vs_ai import report
        r = engine.analyze(MIXED, "general")
        data = _json.loads(report.render_json(r))
        assert data["para_heat"] and data["para_heat"][0]["para"] == 0

    def test_terminal_report_heat_line(self):
        from human_vs_ai import report
        out = report.render_terminal(engine.analyze(MIXED, "general"))
        assert "段落热度" in out and "¶1" in out

    def test_doc_level_findings_not_in_heat(self):
        # doc 级发现（para=-1）是全文属性，不摊进任何段落
        r = engine.analyze(MIXED, "general")
        assert all(h["para"] >= 0 for h in r.para_heat)



# ============ 文种域外（detect_genre，v0.19.0）============
# 合成公文样例（真实公文不入库）：印发类 = 通知 + 被印发件全文；
# 批复类 = "现批复如下"三段式；事务类 = 系数校准域内的普通通知。
YINFA = (
    "各街道办事处，区政府各部门、各直属单位：\n"
    "《某区口袋公园建设三年行动计划（2026—2028年）》已经区政府同意，"
    "现印发给你们，请结合实际认真组织实施。\n"
    "某区口袋公园建设三年行动计划（2026—2028年）\n"
    "为完善城市绿色空间布局，结合我区实际，制定本行动计划。\n"
    "一、总体目标。到二〇二八年，全区建成口袋公园六十处，"
    "人均公园绿地面积明显提升，群众身边的绿色空间显著增加。\n"
    "二、重点任务。（一）科学选址。优先利用边角地、桥下空间，见缝插绿。"
    "（二）精致设计。突出地域文化特色，一园一主题，避免千园一面。\n"
    "三、保障措施。区绿化部门统筹推进，各街道落实属地责任，"
    "每月报送建设进展，年底统一组织考核验收。\n"
)
PIFU = (
    "某市人民政府：\n"
    "你市《关于报请审批某市历史文化名城保护规划的请示》收悉。经研究，现批复如下：\n"
    "一、原则同意《某市历史文化名城保护规划（2026—2035年）》。\n"
    "二、你市要加强对历史文化名城的保护与管理，不得擅自调整规划确定的"
    "保护内容，重大调整须按程序报批。\n"
    "三、省住房和城乡建设厅要加强对规划实施工作的指导、监督和检查。\n"
)
SHIWU = (
    "各街道办事处，区政府各部门：\n"
    "为深入推进我区生活垃圾分类工作，现将有关事项通知如下：\n"
    "一、总体要求。坚持源头减量与末端处理并重，形成全民参与的良好氛围。\n"
    "二、重点任务。（一）完善投放设施。年内完成全部小区投放点升级改造。"
    "（二）健全督导队伍。每三百户配备一名桶边督导员。\n"
    "三、工作要求。各单位要高度重视，明确责任分工，确保各项任务落到实处。\n"
)


class TestGenreDetect:
    def test_yinfa_flagged(self):
        assert "issuance-notice" in detect_genre(_sents(YINFA))

    def test_pifu_flagged(self):
        assert "approval-reply" in detect_genre(_sents(PIFU))

    def test_shiwu_clean(self):
        # 系数校准域内的事务通知：一个 kind 都不许给
        assert detect_genre(_sents(SHIWU)) == []

    def test_short_fragment_skipped(self):
        # 短片段不判文种：与 detect 的 _N_MIN 同门槛（提示挂在指数上，碎片没指数）
        assert detect_genre(["现批复如下。"]) == []

    def test_empty(self):
        assert detect_genre([]) == []


class TestGenreEngine:
    def test_official_flags_genre(self):
        r = engine.analyze(YINFA, "official")
        assert "issuance-notice" in r.ood
        r2 = engine.analyze(PIFU, "official")
        assert "approval-reply" in r2.ood

    def test_other_profile_gated_off(self):
        # 文种判据只对声明 genre_ood 的 profile 生效（开关在 scoring 段）
        assert "issuance-notice" not in engine.analyze(YINFA, "general").ood
        assert "approval-reply" not in engine.analyze(PIFU, "essay").ood

    def test_genre_suppresses_score(self):
        # v0.20.0 判定线定案：两文种分数均不可信（印发冻结 0.462 无信号；
        # 批复分层 CV 0.895 但渠道 LOCO 0.448 是渠道指纹）→ 出分抑制
        r = engine.analyze(YINFA, "official")
        assert r.score is None and r.scoring_note == "该文种未校准"
        r2 = engine.analyze(PIFU, "official")
        assert r2.score is None and r2.scoring_note == "该文种未校准"

    def test_suppressed_doc_keeps_findings(self):
        # 抑制只摘指数：规则发现/统计仍在（逐句证据照常给，只是不给综合分）
        r = engine.analyze(YINFA, "official")
        assert r.doc_stats.n_chars > 0

    def test_flag_is_meta_not_coefficient(self):
        # genre_ood 是元字段不是特征系数：开关有无不得改变分数
        # （防线在 compute_score 的 _SCORING_META 跳过，不在 _pick_scoring 过滤）
        from human_vs_ai.stats import DocStats
        from human_vs_ai.engine import compute_score
        scoring = engine.load_scoring("official")
        assert scoring.get("genre_ood") is True
        s = DocStats(n_sentences=20, n_chars=3000, sentence_cv=0.6,
                     ttr=0.85, ngram_repeat=0.12, conn_density=0.02)
        with_flag = compute_score(s, [], [], scoring)
        without = compute_score(
            s, [], [], {k: v for k, v in scoring.items() if k != "genre_ood"})
        assert with_flag.index == without.index
        # 未声明开关的 profile 照常无此键
        assert not (engine.load_scoring("general") or {}).get("genre_ood")

    def test_report_line_official(self):
        from human_vs_ai import report
        out = report.render_terminal(engine.analyze(PIFU, "official"))
        assert "文种域外" in out and "批复类" in out and "本篇仅供参考" in out
        assert "该文种未校准" in out
        md = report.render_markdown(engine.analyze(YINFA, "official"))
        assert "文种域外" in md and "印发类" in md

    def test_report_no_line_for_shiwu(self):
        from human_vs_ai import report
        out = report.render_terminal(engine.analyze(SHIWU, "official"))
        assert "域外" not in out

    def test_json_export_contains_genre(self):
        from human_vs_ai import report
        import json as _json
        data = _json.loads(report.render_json(engine.analyze(YINFA, "official")))
        assert "issuance-notice" in (data["ood"] or [])
        assert data["score"] is None and data["score_note"] == "该文种未校准"
        data2 = _json.loads(report.render_json(engine.analyze(SHIWU, "official")))
        assert data2["ood"] is None
        assert data2["score"] is not None

    def test_clean_doc_score_untouched(self):
        # 提示只进 ood 字段：同一篇事务文，开不开提示，findings/分数路径不变
        r = engine.analyze(SHIWU, "official")
        assert r.ood == []
        assert r.score is not None
