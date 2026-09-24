"""多文体 profile 测试：news/essay/review 的加载、评分、词表纪律与 collect。

运行：python -m pytest tests/ -q
"""
import json

import pytest

from human_vs_ai import cli, collect, engine

NEWS_AI = ("近日，某市发布了新政策。值得注意的是，方案将于下月施行。"
           "首先，召开了动员会。其次，开展了宣传。此外，建立了台账。"
           "与此同时，强化了督导。另外，还组织了专项培训。"
           "综上所述，各项工作有序推进。下一步将持续深化落实。")
ESSAY_AI = ("在这个日新月异的时代，坚持显得尤为珍贵。这不仅是一次尝试，更是对自我的超越。"
            "首先，我们要明确目标。其次，我们要持之以恒。正如古人所言，千里之行始于足下。"
            "综上所述，唯有坚持方能谱写青春华章。让我们携手共进，绽放属于自己的光彩。"
            "因此，我们要勇敢追梦。人生没有白走的路。每一步都算数。")
REVIEW_TEXT = "这部电影镜头语言精妙，讲述了主人公的成长故事，值得回味。"


class TestNewProfiles:
    def test_seven_profiles_available(self):
        assert engine.available_profiles() == [
            "academic", "essay", "general", "news", "official", "personal", "review"]

    def test_news_scoring_and_lexicon(self):
        r = engine.analyze(NEWS_AI, "news")
        assert r.score, "news 出分（过 8 句门槛）"
        assert r.score.human_p50 == 7 and r.score.human_p90 == 61
        ids = {f.rule_id for f in r.findings}
        assert "N-RECENT-01" in ids

    def test_news_triad_excluded(self):
        # 砍掉清单：三连排比在新闻域反向（真人 71 vs AI 35），不得进 news 库
        rule_ids = {r.id for r in engine.load_rules("news")}
        assert "G-TRIAD-01" not in rule_ids

    def test_general_dash_excluded(self):
        # 砍掉清单：破折号高频在问答域反向（真人 1.8% vs AI 0.7%，
        # _qa/general-contemporary.md），不得进 general 库
        rule_ids = {r.id for r in engine.load_rules("general")}
        assert "D-DASH-01" not in rule_ids

    def test_essay_scoring_and_negation_high(self):
        r = engine.analyze(ESSAY_AI, "essay")
        assert r.score and round(r.score.auroc, 3) == 0.952
        ids = {f.rule_id for f in r.findings}
        assert "E-NEGA-01" in ids and "E-SUB-01" in ids
        # 否定式拔高在本库为 high（作文域最强单项）
        sev = {r.id: r.severity for r in engine.load_rules("essay")}
        assert sev["E-NEGA-01"] == "high"

    def test_review_no_scoring_by_design(self):
        long_review = REVIEW_TEXT + "总体而言完成度不错。" * 7
        r = engine.analyze(long_review, "review")
        assert r.score is None
        assert r.scoring_note == "该文体未校准"
        ids = {f.rule_id for f in r.findings}
        assert "R-ANALYT-01" in ids

    def test_news_thresholds_tiered(self):
        rule = next(r for r in engine.load_rules("news") if r.id == "D-UNIF-01")
        assert rule.doc_tiers and rule.doc_tiers[0][0] == 300


class TestCollect:
    def test_sanitize_pii(self):
        s = collect.build_sample(
            "联系 13812345678 或 a@b.com，证件 110101199001011234，卡号 6222020200112233445",
            "news", "fp")
        assert "【手机号】" in s["text"] and "13812345678" not in s["text"]
        assert "【邮箱】" in s["text"] and "【身份证号】" in s["text"]
        assert "【卡号】" in s["text"]
        # 超长数字串（订单号等，非 16-19 位标准卡号）同样整段打码（宁枉勿纵）
        s2 = collect.build_sample("订单号 622202020011223345678 已提交", "news", "hit")
        assert "【卡号】" in s2["text"] and "622202020011223345678" not in s2["text"]
        assert s["label"] == "fp" and s["profile"] == "news"
        assert len(s["text_sha256"]) == 16

    def test_cli_collect_jsonl(self, tmp_path, capsys):
        f = tmp_path / "a.md"
        f.write_text(NEWS_AI, encoding="utf-8")
        cli.main(["collect", str(f), "-p", "news", "--label", "hit"])
        line = capsys.readouterr().out.strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["type"] == "human-vs-ai-calibration"
        assert payload["label"] == "hit" and payload["profile"] == "news"
        assert "NEWS_AI" not in payload["text"] and payload["n_chars"] > 0

    def test_collect_label_validated(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("内容。", encoding="utf-8")
        with pytest.raises(SystemExit):
            cli.main(["collect", str(f), "--label", "wrong"])
