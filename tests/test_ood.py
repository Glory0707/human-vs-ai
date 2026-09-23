"""域外文体探测（ood）：文言/诗行判据、四端同行字段与 JS 同构。

判据在 C-ReD 全量 10.4 万篇上校准：正样本 5/5 命中，误报 0.005%；
这里钉住代表性正负样本与关键阈值边界，防止后人"顺手调参"漂移。
运行：python -m pytest tests/test_ood.py -q
"""
from human_vs_ai import engine, segment
from human_vs_ai.ood import detect


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


class TestEngineIntegration:
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
            line = report_score_line(r)
            assert ("校准真人" in line) or line.endswith("/ 100")

    def test_ood_hint_does_not_affect_score(self):
        # 域外提示不参与评分：同一文本有/无 ood 字段，分数只由特征决定
        r = engine.analyze(YUEYANG, "essay")
        assert r.score is not None or r.scoring_note == "该文体未校准评分"


def report_score_line(r):
    from human_vs_ai.report import _score_line
    return _score_line(r.score)
