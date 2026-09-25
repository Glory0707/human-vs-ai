"""personal profile（口味校准层）测试。

分两层：
- 合成样例层：入库，任何环境都能跑（开源侧只能出现合成样例）
- 私库回归层：读 corpus_private/，语料缺失时跳过——开放环境不阻塞

合成样例是照着 docs/taste_zhouao.md 的判别式自造的同类句，不是语料原文。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from human_vs_ai import engine, rewrite

ROOT = Path(__file__).parent.parent
CORPUS = ROOT / "corpus_private"

# 每个口味条目的合成样例（自造，与语料原文无关）
SYNTHETIC_HITS = {
    "T1": "别急，代码明天还在仓库里。",
    "T2": "窗口一开，就是你的战场。",
    "T3": "发呆不是偷懒，是给大脑整理碎片。",
    "T4": "早起十分钟，一天效率翻倍。",
    "T5": "夜深了，字也看不清了，早点睡。",
    "T6": "你比太阳还早起。",
    "T7": "深夜的你，还在改这一版。",
    "T8": "周一上午，打工人的至暗时刻。",
    "T9": "说好只改一行——你上次也是这么说的。",
    "T10": "继续加油，你可以的。",
    "T11": "自动匹配相关段落，一次最多三条。",
    "T12": "总而言之，这版更稳。",
}


class TestPersonalProfile:
    def test_profile_has_taste_ids(self):
        rules = engine.load_rules("personal")
        assert len(rules) >= 15
        assert all(r.taste for r in rules), "personal 每条规则都要标口味条目编号"

    @pytest.mark.parametrize("taste", sorted(SYNTHETIC_HITS))
    def test_synthetic_example_hits_its_taste(self, taste):
        text = SYNTHETIC_HITS[taste]
        result = engine.analyze(text, "personal")
        got = {f.taste for f in result.findings + result.hints}
        assert taste in got, f"{taste} 合成样例未被检出：{text}"

    def test_finding_carries_taste(self):
        result = engine.analyze("窗口一开，就是你的战场。", "personal")
        f = (result.findings + result.hints)[0]
        assert f.taste == "T2"
        assert f.to_dict()["taste"] == "T2"

    def test_json_report_includes_taste(self):
        import json as _json
        from human_vs_ai import report
        result = engine.analyze("窗口一开，就是你的战场。", "personal")
        payload = _json.loads(report.render_json(result))
        assert payload["findings"][0]["taste"] == "T2"


class TestRewriteRules:
    def test_r1_protects_data_and_conclusions(self):
        # 删减哲学最高优先：含数字/结论的句子一律保留，结构不动
        for line in ["研究显示，准确率从 0.71 提升到 0.89。",
                     "实测三次，失败率降到 3%。",
                     "结论：该方案通过了 12 项验收。"]:
            adv = rewrite.classify_line(line)
            assert adv.action == rewrite.KEEP, f"R1 未保护：{line} → {adv.action}"

    def test_r1_protects_fullwidth_digits(self):
        # 中文文案里全角数字常见；判据显式列 ０-９，与 JS 端字符类逐字一致
        adv = rewrite.classify_line("已完成３轮内测。")
        assert adv.action == rewrite.KEEP

    def test_manual_voice_deleted_whole(self):
        # 功能说明腔：整句删，不压缩、不产出候选
        adv = rewrite.classify_line("点击右上角选择文件，支持批量导入。")
        assert adv.action == rewrite.DELETE
        assert adv.candidate == ""
        assert "整句删" in adv.direction

    def test_comfort_cut_to_fact(self):
        # 劝慰腔：砍掉逗号后的安慰半句
        adv = rewrite.classify_line("别急，代码明天还在仓库里。")
        assert adv.action == rewrite.REWRITE
        assert adv.candidate == "别急。"

    @pytest.mark.parametrize("line", [
        "你已经很棒了，不要给自己太大压力。",
        "记得好好爱自己，你值得世间所有美好。",
        "无论结果如何，我都会一直陪着你。",
        "忙碌的日子里，也请记得照顾好自己的身体哦。",
    ])
    def test_generic_llm_comfort_detected_and_deleted(self, line):
        # 通用 LLM 关怀腔（种子池之外的高频形态）：T1 必须检出，
        # 且整句没有事实半句可留——进删档，不能截出残句
        result = engine.analyze(line, "personal")
        assert "T1" in {f.taste for f in result.findings + result.hints}
        adv = rewrite.classify_line(line)
        assert adv.action == rewrite.DELETE, f"应整句删，实际 {adv.action}"
        assert not adv.candidate

    def test_line_with_meme_is_kept(self):
        # 合成样例：含梗 + 具体名词，未命中腔调 → 保留
        adv = rewrite.classify_line("摸鱼一时爽，组会火葬场。")
        assert adv.action == rewrite.KEEP

    def test_generic_line_kept_with_r3_hint(self):
        # R3 只作提示不作判据：无腔调也没具体名词 → 保留，但给"可补名词/梗"的方向
        # （单独拿"缺名词"定罪会把功能标签与 help 文本全判成该改——自检实证）
        adv = rewrite.classify_line("让每一次操作都得心应手")
        assert adv.action == rewrite.KEEP
        assert "具体名词" in adv.direction

    def test_rewrite_text_is_line_based(self):
        result = rewrite.rewrite_text("第一行，什么都好。\n\n第二行，也不错。")
        assert len(result.advices) == 2

    def test_render_advice_mentions_criterion(self):
        out = rewrite.render_advice(rewrite.rewrite_text("别急，代码明天还在仓库里。"))
        assert "改" in out and "重要数据和结论要保留" in out


class TestCliRewrite:
    def test_cli_rewrite_runs(self, tmp_path):
        f = tmp_path / "copy.txt"
        f.write_text("别急，代码明天还在仓库里。\n实测三次，失败率降到 3%。\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "human_vs_ai.cli", "rewrite", str(f)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        assert "保留 1" in proc.stdout
        assert "重要数据和结论要保留" in proc.stdout

    def test_cli_rewrite_json(self, tmp_path):
        f = tmp_path / "copy.txt"
        f.write_text("自动匹配相关段落，一次最多三条。\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "human_vs_ai.cli", "rewrite", str(f), "-f", "json"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["advices"][0]["action"] == "删"
        assert payload["advices"][0]["taste"] == ["T11"]


# ---------- 私库回归层（语料缺失自动跳过） ----------

requires_corpus = pytest.mark.skipif(
    not (CORPUS / "taste_reference.json").exists(),
    reason="私库语料未就位（corpus_private/ 不入库，开源环境跳过）",
)


@requires_corpus
class TestPrivateRegression:
    def test_taste_vetoed_recall_is_total(self):
        ref = json.loads((CORPUS / "taste_reference.json").read_text(encoding="utf-8"))
        missed = []
        for line in ref["vetoed"]:
            r = engine.analyze(line, "personal")
            if not (r.findings or r.hints):
                missed.append(line)
        assert not missed, f"被毙稿漏检 {len(missed)} 条：{missed}"

    def test_taste_kept_lines_not_hard_flagged(self):
        ref = json.loads((CORPUS / "taste_reference.json").read_text(encoding="utf-8"))
        hard = []
        for line in ref["kept"]:
            r = engine.analyze(line, "personal")
            if any(f.severity in ("high", "medium") for f in r.findings):
                hard.append(line)
        assert not hard, f"定稿被 high/medium 误报：{hard}"

    def test_my_rewrites_not_flagged_as_ai(self):
        ref = json.loads((CORPUS / "taste_reference.json").read_text(encoding="utf-8"))
        flagged = []
        for item in ref["rewrites"]:
            if item["ai"].startswith("（新增"):
                continue
            r = engine.analyze(item["mine"], "personal")
            if any(f.severity in ("high", "medium") for f in r.findings):
                flagged.append(item["mine"])
        assert not flagged, f"我亲改的定稿被判 AI 味：{flagged}"

    def test_rewrite_dimension_rate(self):
        ref = json.loads((CORPUS / "taste_reference.json").read_text(encoding="utf-8"))
        hit = total = 0
        for item in ref["rewrites"]:
            if item["ai"].startswith("（新增"):
                continue
            total += 1
            hit += 1 if item["dims"]["hit"] else 0
        assert total and hit / total >= 0.8, f"改写维度三中其二 {hit}/{total} < 80%"


@requires_corpus
def test_no_private_corpus_leak_in_repo_files():
    """硬边界：入库文件里不许出现私人语料的 6 字级片段。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_private_leak.py")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"检测到私人语料泄漏：\n{proc.stdout}"


class TestRewriteNoopGuard:
    def test_midsentence_closer_is_removed(self):
        # 视觉走查发现的 bug：多句段落里"综上所述"在行中，
        # 旧行首锚定剥不掉 → 候选与原文等值，"改了等于没改"
        line = ("首先，本文制备了系列的钙钛矿样品。其次，我们对样品进行了全面的表征分析。"
                "最后，系统地测试了光电性能。综上所述，本文开展了一系列深入而有效的研究工作。")
        adv = rewrite.classify_line(line)
        assert adv.action == rewrite.REWRITE
        assert adv.candidate, "必须有候选"
        assert "综上所述" not in adv.candidate
        assert adv.candidate != line

    def test_noop_candidate_never_returned(self):
        # 万能护栏：任何规则产出的候选与原文等值时，必须降级为方向提示
        line = "不得不说，这版更稳。"
        adv = rewrite.classify_line(line)
        if adv.candidate:
            assert "".join(ch for ch in adv.candidate if ch not in "。！？～ ") !=                    "".join(ch for ch in line if ch not in "。！？～ ")

    def test_multisentence_paragraph_gets_direction_not_truncation(self):
        # 视觉走查发现的 bug：T2 截半句候选作用在多句段落上，
        # 会把整段毁成第一个逗号前的碎片
        para = ("在当今快速发展的时代背景下，能源问题日益凸显。与此同时，传统的硅基太阳能电池"
                "逐渐接近其理论效率极限。因此，开发新型低成本的光伏技术显得尤为重要。此外，"
                "钙钛矿太阳能电池作为一种新兴的光伏技术，扮演着越来越重要的角色，有望在未来"
                "的能源格局中发挥关键作用。")
        adv = rewrite.classify_line(para)
        assert adv.candidate == "", "多句段落不许给截半句候选"
        assert adv.direction
        # 单句行为不受影响：仍是截半句候选
        single = rewrite.classify_line("窗口一开，就是你的战场。")
        assert single.candidate == "窗口一开。"


class TestApplyEdits:
    """清理稿（apply_edits）：删档删行、改档换候选、其余原样，行号对位。"""

    TEXT = ("自动匹配相关内容，一次最多 3 篇。\n"
            "\n"
            "- 别急，代码明天还在仓库里。\n"
            "已完成 3 轮内测，通过率 100%。\n"
            "深夜的你，还在改稿。\n")

    def test_line_field_maps_to_source_lines(self):
        r = rewrite.rewrite_text(self.TEXT)
        lines = self.TEXT.splitlines()
        for a in r.advices:
            assert lines[a.line].strip().lstrip("-*0123456789.、) \t").endswith(a.text), \
                f"行号失配：advice.line={a.line}"

    def test_draft_drops_deletes_swaps_candidates_keeps_rest(self):
        r = rewrite.rewrite_text(self.TEXT)
        draft = rewrite.apply_edits(r)
        out_lines = draft.splitlines()
        # 空行与保留档原样还在
        assert out_lines[0] == self.TEXT.splitlines()[0]   # R1 保护行
        assert out_lines[1] == ""                          # 空行
        assert "已完成 3 轮内测" in draft                   # 保留档
        # 改档换成候选
        assert "代码明天还在仓库里" not in draft
        # 每个改档候选都出现在稿子里
        for a in r.advices:
            if a.action == rewrite.REWRITE and a.candidate:
                assert a.candidate in draft

    def test_deleted_line_removed_entirely(self):
        r = rewrite.rewrite_text("点击右上角选择文件，支持批量导入。\n保留这行。\n")
        assert r.advices[0].action == rewrite.DELETE
        draft = rewrite.apply_edits(r)
        assert draft == "保留这行。\n"

    def test_exotic_line_separators_pass_through(self):
        #  / // 也是 splitlines 行界：无删改时清理稿
        # 必须逐字等于原文（终结符原样）；有删改时只动目标行
        for sep in (" ", " ", "", ""):
            text = f"保留这行。{sep}点击右上角选择文件，支持批量导入。{sep}也保留。{sep}"
            r = rewrite.rewrite_text(text)
            assert rewrite.apply_edits(r) == f"保留这行。{sep}也保留。{sep}"

    def test_crlf_endings_preserved(self):
        r = rewrite.rewrite_text("点击右上角选择文件，支持批量导入。\r\n保留这行。\r\n")
        draft = rewrite.apply_edits(r)
        assert draft == "保留这行。\r\n"

    def test_counts(self):
        r = rewrite.rewrite_text(self.TEXT)
        deleted, changed = rewrite.apply_counts(r)
        assert (deleted, changed) == (
            sum(1 for a in r.advices if a.action == rewrite.DELETE),
            sum(1 for a in r.advices if a.action == rewrite.REWRITE and a.candidate))

    def test_cli_rewrite_apply(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("点击右上角选择文件，支持批量导入。\n保留这行。\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "human_vs_ai.cli", "rewrite", str(f), "--apply"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "保留这行。"
