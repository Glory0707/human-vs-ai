"""漂移监测（tools/drift_monitor.py）测试：聚合、对比、漂移检出。

用合成样本跑通全流程——样本本体不入库（corpus_private/ 在 gitignore），
测试里临时生成。
运行：python -m pytest tests/test_drift.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import pytest

from drift_monitor import aggregate, compare, load_samples


def sample(profile="general", month="2026-08", score=30, findings=None):
    return {"type": "human-vs-ai-calibration", "version": "0.17.0",
            "time": month, "label": "hit", "profile": profile,
            "text": "样例文本。", "score": score, "findings": findings or []}


class TestAggregate:
    def test_group_by_profile_month(self):
        groups = aggregate([sample(), sample(score=50), sample(month="2026-09")],
                           by_month=True)
        assert set(groups) == {"general|2026-08", "general|2026-09"}
        assert groups["general|2026-08"]["n"] == 2

    def test_profile_level_default(self):
        # 对比在 profile 级做：漂移是跨月的，按月分组就测不到了（实证）
        groups = aggregate([sample(), sample(month="2026-09", score=50)])
        assert set(groups) == {"general"}
        assert groups["general"]["n"] == 2

    def test_score_stats(self):
        groups = aggregate([sample(score=10), sample(score=30), sample(score=90)])
        g = groups["general"]
        assert g["p50"] == 30 and g["p90"] == 90
        assert g["score_rate"] == 1.0

    def test_rule_rates(self):
        groups = aggregate([sample(findings=["G-TRIAD-01"]),
                            sample(findings=["G-TRIAD-01", "G-SAFE-01"])])
        rules = groups["general"]["rules"]
        assert rules["G-TRIAD-01"] == pytest.approx(1.0)
        assert rules["G-SAFE-01"] == pytest.approx(0.5)

    def test_unscored_excluded_from_percentile(self):
        groups = aggregate([sample(score=None), sample(score=40)])
        assert groups["general"]["p50"] == 40


class TestCompare:
    def test_p50_drift_detected(self):
        old = aggregate([sample(score=10)] * 10)
        new = aggregate([sample(month="2026-09", score=40)] * 10)
        signals = compare(old, new)
        assert any("指数 p50" in s for s in signals)

    def test_rule_drift_detected(self):
        old = aggregate([sample()] * 10)
        new = aggregate([sample(month="2026-09", findings=["G-TRIAD-01"])] * 10)
        signals = compare(old, new)
        assert any("G-TRIAD-01" in s for s in signals)

    def test_stable_no_signal(self):
        rows = [sample(score=30)] * 10
        assert compare(aggregate(rows), aggregate(rows)) == []

    def test_new_group_only_no_compare(self):
        old = aggregate([sample()])
        new = aggregate([sample(month="2026-09")])
        assert compare(old, new) == []


class TestLoad:
    def test_bad_rows_skipped(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text("not json\n" + json.dumps(sample(), ensure_ascii=False) + "\n",
                     encoding="utf-8")
        rows = load_samples([p])
        assert len(rows) == 1

    def test_wrong_type_skipped(self, tmp_path):
        p = tmp_path / "s.jsonl"
        bad = sample()
        bad["type"] = "other"
        p.write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
        assert load_samples([p]) == []
