"""句级困惑度：纯函数口径与依赖隔离（模型相关路径不打进 CI）。

运行：python -m pytest tests/test_ppl.py -q
"""
import math

import pytest

from human_vs_ai import ppl


class TestNllToStats:
    def test_uniform_zero_nll_is_ppl_one(self):
        s = ppl.nll_to_stats([0.0, 0.0])
        assert s["ppl"] == pytest.approx(1.0)
        assert s["avg_nll"] == 0.0
        assert s["n_tokens"] == 2

    def test_ln4_average_is_ppl_four(self):
        s = ppl.nll_to_stats([math.log(4)] * 2)
        assert s["ppl"] == pytest.approx(4.0)
        assert s["avg_nll"] == pytest.approx(math.log(4))

    def test_empty_returns_none(self):
        assert ppl.nll_to_stats([]) is None

    def test_extreme_avg_clamped(self):
        # exp 溢出防护：avg 被钳在 50，PPL 封顶 e^50
        s = ppl.nll_to_stats([1000.0])
        assert s["ppl"] == pytest.approx(math.exp(50.0))


class TestDepsIsolation:
    def test_module_import_never_pulls_torch(self):
        # import 必须零副作用：torch 缺失的环境（CI）也要能收集用例
        import importlib
        importlib.reload(ppl)

    def test_check_deps_matches_env(self):
        import importlib
        try:
            importlib.import_module("torch")
            has = True
        except ImportError:
            has = False
        if has:
            ppl.check_deps()  # 有依赖：静默通过
        else:
            with pytest.raises(ImportError, match="human-vs-ai"):
                ppl.check_deps()


class TestRender:
    def test_render_table_and_summary(self):
        sentences = ["顺滑的第一句。", "第二句绕了点弯子。", "第三句。"]
        stats = [
            {"ppl": 5.0, "avg_nll": math.log(5.0), "n_tokens": 9},
            {"ppl": 40.0, "avg_nll": math.log(40.0), "n_tokens": 10},
            {"ppl": 20.0, "avg_nll": math.log(20.0), "n_tokens": 4},
        ]
        out = ppl.render(sentences, stats, "Qwen/Qwen3-0.6B")
        assert "句级困惑度（Qwen/Qwen3-0.6B）" in out
        assert "中位 PPL 20.0" in out
        assert "最顺滑 1 句" in out and "5.0" in out

    def test_render_none_stats_skipped(self):
        out = ppl.render(["。"], [None], "m")
        assert "无可打分的句子" in out
