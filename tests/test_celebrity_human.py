"""名人真文回归集：公开报道过的"真人名作被判 AI"误伤案例，钉进测试做基线。

钉住的是可查证的公版原文 + 可查证的公开误伤事件，防两类回归：
引擎校准让名作误伤静默恶化；或重构悄悄改变了这类文本的判定路径。

- 老舍《林海》（1961）：南都大数据研究院 2025-06 实测十款国产 AIGC 检测
  工具（40 次测试），茅茅虫把老舍原著判 99.9% AI、万方判 35.6%
  （https://www.secrss.com/articles/79617）。本引擎 essay 档同样给出高分
  （E-NEGA-01"不是…而是"打在文学排比上、句长 CV 低统计工整）——统计
  底盘对文学散文天然敏感，这是"检测器测的是文风不是作者"的活案例。
- 朱自清《荷塘月色》（1927）：被某常用论文检测系统判 AI 生成内容疑似度
  62.88%（大河报 2025-05 报道，人民日报/新京报/中青报跟进；同例《流浪
  地球》片段 52.88%）。
- 斯坦福 Patterns 2023：七款英文检测器把非母语者托福作文平均误判 61.3%
  （Liang et al., doi:10.1016/j.patter.2023.100779）——本引擎对非中文文本
  的答案是不判（v0.30.0 抑制出分），行为一并钉死。

文本来源（均为公有领域：老舍 1966 年、朱自清 1948 年逝世，著作权已到期）：
- 《林海》录自《内蒙风光》（老舍文集附录，dooshu.github.io/cn/1490），
  开头、"云横秦岭"、"兴安岭上千般宝"、"深的浅的明的暗的"四处与
  QQ 阅读《老舍散文》、百度知道、新学网独立片段逐一核对一致。
- 《荷塘月色》录自 GitHub article-gen 语料库（text/modern/），全文与
  教材版本核对，开头与清华官网节选、维基文库、中大人文电算库逐句吻合；
  源文件两处"现 在"转录空格已修正。
"""
from pathlib import Path

from human_vs_ai import engine

DATA = Path(__file__).parent / "data" / "celebrity"
LINHAI = (DATA / "linhai_laoshe_1961.txt").read_text(encoding="utf-8")
HETANG = (DATA / "hetang_yuese_zhu1927.txt").read_text(encoding="utf-8")

# 南都误报线口径：≥60 即"误报"区间。基线钉现象（名作在 essay 档会进
# 误报区间），不钉单点分数——单点会被任何正当校准挪动，现象挪走才该改测试。


def test_linhai_is_falsely_high_on_essay():
    """老舍《林海》在 essay 档进误报区间——南都十款工具同款误伤的基线。"""
    r = engine.analyze(LINHAI, "essay")
    assert r.score is not None
    assert r.doc_stats.n_sentences >= 8
    assert 60 <= round(r.score.index) <= 85, round(r.score.index)


def test_hetang_is_falsely_high_on_essay():
    """朱自清《荷塘月色》在 essay 档进误报区间——62.88% 标注事件的基线。"""
    r = engine.analyze(HETANG, "essay")
    assert r.score is not None
    assert r.doc_stats.n_sentences >= 8
    assert 60 <= round(r.score.index) <= 85, round(r.score.index)


def test_findings_are_style_level_not_crash():
    """名作的命中全部是风格层发现（带解释与建议），引擎路径完整不崩。"""
    for text in (LINHAI, HETANG):
        r = engine.analyze(text, "essay")
        for f in r.findings:
            assert f.rule_id and f.rule_name and f.explanation
        assert r.ood is not None


def test_english_toefl_style_suppressed():
    """斯坦福 61.3% 误伤非母语者的本工具答案：非中文不判（抑制出分）。"""
    toefl_style = (
        "In recent years, the rapid development of technology has profoundly changed "
        "the way people live and work. Many scholars believe that artificial intelligence "
        "will play an increasingly important role in education. Firstly, AI can provide "
        "personalized learning experiences for students. Secondly, it helps teachers save "
        "time on repetitive tasks. Thirdly, online platforms make knowledge more accessible "
        "than ever before. In conclusion, we should embrace these changes while remaining "
        "aware of the potential challenges. Moreover, society must ensure that technology "
        "serves humanity rather than replaces it. Education systems around the world are "
        "already adapting to this new reality, and students benefit greatly from these innovations."
    )
    r = engine.analyze(toefl_style, "general")
    assert r.score is None
    assert "非中文" in (r.scoring_note or "")
