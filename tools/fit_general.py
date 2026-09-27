"""general 评分拟合：知乎真实长回答 vs gen2026 当季模型回答。

复现 v0.17.8 当代重拟合（此前系数无脚本留档），v0.30.0 起作为 general
评分的可复现出口。语料与拟合集零重叠约束见 tools/oos_check.py：
- 真人：_qa/corpus/zhihu/zhihu-answers.json（过 8 句门槛者）
- AI：_qa/corpus/gen2026/qa.jsonl（2026 当季 9 模型 600 字回答，过 8 句门槛者）

产出可直接粘贴进 general.yaml 的 scoring 段。

用法：python tools/fit_general.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from human_vs_ai import engine  # noqa: E402
from fit_score_tiers import FEATS, auroc, fit, holdout  # noqa: E402

ZHIHU = ROOT / "_qa/corpus/zhihu/zhihu-answers.json"
GEN = ROOT / "_qa/corpus/gen2026/qa.jsonl"


def extract(text: str) -> dict:
    r = engine.analyze(text, "general")
    st = r.doc_stats
    n = max(st.n_sentences, 1)
    ungated = sum(engine._SCORE_WEIGHT.get(f.severity, 1.0)
                  for f in r.findings + r.hints if f.para >= 0)
    return {
        "n_chars": st.n_chars,
        "n_sentences": st.n_sentences,
        "hit_density": ungated / n,
        "sentence_cv": st.sentence_cv,
        "ttr": st.ttr,
        "ngram_repeat": st.ngram_repeat,
    }


def load_rows() -> list[dict]:
    rows = []
    for d in json.loads(ZHIHU.read_text(encoding="utf-8")):
        e = extract(d["text"])
        if e["n_sentences"] < 8:
            continue
        e["_ai"] = False
        rows.append(e)
    for line in GEN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = extract(json.loads(line)["text"])
        if e["n_sentences"] < 8:
            continue
        e["_ai"] = True
        rows.append(e)
    return rows


def main() -> None:
    rows = load_rows()
    ai = [r for r in rows if r["_ai"]]
    hu = [r for r in rows if not r["_ai"]]
    print(f"general 拟合样本：AI {len(ai)} / 真人 {len(hu)}（均过 8 句门槛）")

    model = fit(rows, FEATS, balance=True)
    if model is None:
        sys.exit("拟合失败")
    coef, intercept = model[0], model[1]
    print("\n拟合系数（general.yaml scoring 段）：")
    print(f"intercept: {intercept:.4f}")
    for f, c in zip(FEATS, coef):
        print(f"{f}: {c:.4f}")

    def score(r, coef, intercept):
        z = intercept
        for c, f in zip(coef, FEATS):
            v = r.get(f)
            if v is None or v != v:
                continue
            z += c * v
        return z

    all_auc = auroc([score(r, coef, intercept) for r in rows if r["_ai"]],
                    [score(r, coef, intercept) for r in rows if not r["_ai"]])
    print(f"\n全量 AUROC: {all_auc:.3f}")

    aucs = []
    for seed in range(10):
        auc_h = holdout(rows, FEATS, balance=True, seed=seed + 1)
        if auc_h == auc_h:
            aucs.append(auc_h)
    if aucs:
        print(f"分层留出 ×10: 均值 {statistics.mean(aucs):.3f}（min {min(aucs):.3f} / max {max(aucs):.3f}）")

    sig = lambda z: 100 / (1 + 2.718281828 ** (-z))
    hu_scores = sorted(sig(score(r, coef, intercept)) for r in hu)
    print(f"真人指数分位: p50 {hu_scores[len(hu_scores)//2]:.0f} / p90 {hu_scores[min(int(len(hu_scores)*0.9), len(hu_scores)-1)]:.0f}")
    print("建议 YAML：intercept {:.4f} / ".format(intercept)
          + " / ".join(f"{f} {c:.4f}" for f, c in zip(FEATS, coef)))


if __name__ == "__main__":
    main()
