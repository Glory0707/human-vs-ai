"""official 评分拟合：真人事务公文（gov 抓取集）vs AI 公文（gen2026）。

v0.17.7 起真人侧达到拟合规模：`_qa/corpus/gov/` 87 篇有效（≥300 字），
过 8 句门槛 71 篇；AI 侧 gen2026/official.jsonl 70 篇全部过门槛。
类平衡逻辑回归（复用 fit_score_tiers 底座），半样本留出验证。

产出可直接粘贴进 official.yaml 的 scoring 段；数字写入 _qa/official-contemporary.md。

用法:python tools/fit_official.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from human_vs_ai import engine  # noqa: E402
from fit_score_tiers import FEATS, auroc, fit, holdout  # noqa: E402


def extract(text: str, profile: str) -> dict:
    r = engine.analyze(text, profile)
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
        "conn_density": st.conn_density,
    }


def load_rows() -> list[dict]:
    rows = []
    # AI：gen2026 公文
    for line in (ROOT / "_qa/corpus/gen2026/official.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = json.loads(line)["text"].strip()
        e = extract(t, "official")
        if e["n_sentences"] < 8:
            continue
        e["_ai"] = True
        rows.append(e)
    # 真人：gov 抓取集（fetch_gov_corpus 与 expand_gov_corpus 同格式）
    for f in sorted((ROOT / "_qa/corpus/gov").glob("*.txt")):
        body = f.read_text(encoding="utf-8").split("\n\n", 1)[-1].strip()
        if len(body) < 300:
            continue
        e = extract(body, "official")
        if e["n_sentences"] < 8:
            continue
        e["_ai"] = False
        rows.append(e)
    return rows


def main() -> None:
    rows = load_rows()
    ai = [r for r in rows if r["_ai"]]
    hu = [r for r in rows if not r["_ai"]]
    print(f"official 拟合样本：AI {len(ai)} / 真人 {len(hu)}（均过 8 句门槛）")

    # 单特征方向检查
    for feat in FEATS + ["conn_density"]:
        vals_ai = [r[feat] for r in rows if r["_ai"] and r[feat] == r[feat]]
        vals_hu = [r[feat] for r in rows if not r["_ai"] and r[feat] == r[feat]]
        if not vals_ai or not vals_hu:
            print(f"  {feat}: 样本不足")
            continue
        auc = auroc(vals_ai, vals_hu)
        import statistics
        print(f"  {feat}: AUROC {auc:.3f}（AI 中位 {statistics.median(vals_ai):.3f} vs 真人 {statistics.median(vals_hu):.3f}）")

    # 全量拟合（类平衡）
    model = fit(rows, FEATS, balance=True)
    if model is None:
        sys.exit("拟合失败")
    coef, intercept = model[0], model[1]
    print("\n拟合系数（official.yaml scoring 段）：")
    print(f"intercept: {intercept:.4f}")
    for f, c in zip(FEATS, coef):
        print(f"{f}: {c:.4f}")

    # 全量 AUROC（用 apply_model 语义：z = intercept + coef·x）
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

    # 分层留出 ×10
    import statistics
    aucs = []
    for seed in range(10):
        auc_h = holdout(rows, FEATS, balance=True, seed=seed + 1)
        if auc_h == auc_h:
            aucs.append(auc_h)
    if aucs:
        print(f"分层留出 ×10: 均值 {statistics.mean(aucs):.3f}（min {min(aucs):.3f} / max {max(aucs):.3f}）")

    # 真人分位（供 human_p50/p90 锚点）
    hu_scores = sorted(100 / (1 + pow(2.718281828, -score(r, coef, intercept))) for r in hu)
    p50 = hu_scores[len(hu_scores) // 2]
    p90 = hu_scores[min(int(len(hu_scores) * 0.9), len(hu_scores) - 1)]
    print(f"\n真人指数分位: p50 {p50:.0f} / p90 {p90:.0f}")
    print(f"建议 YAML：intercept {intercept:.4f} / " + " / ".join(f"{f} {c:.4f}" for f, c in zip(FEATS, coef)))


if __name__ == "__main__":
    main()
