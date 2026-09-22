"""新域评分拟合：essay / news 的 AI 味指数系数（类平衡逻辑回归 + 分层留出）。

与 fit_score_tiers 同一套数学（fit/auroc/holdout 直接复用），语料换成
C-ReD 对应域全量（真人 + 9 生成器，≥120 字且过 8 句门槛）。产出可直接
粘贴进 profile YAML 的 scoring 段。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from human_vs_ai import engine  # noqa: E402
from fit_score_tiers import FEATS, auroc, fit, holdout, apply_model  # noqa: E402

DOMAINS = {
    "essay": ("composition", ("deepseek-r1", "deepseek-v3", "doubao-1.5-pro",
                              "gemini-2.5-flash", "gpt-4o", "claude-3.5-haiku", "qwen-3")),
    "news": ("news", ("deepseek-r1", "deepseek-v3", "doubao-1.5-pro",
                      "gemini-2.5-flash", "gpt-4o", "claude-3.5-haiku", "qwen-3")),
}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, choices=sorted(DOMAINS))
    ap.add_argument("--sample", type=int, default=400, help="每文件抽头数（0=全量）")
    args = ap.parse_args()

    prefix, models = DOMAINS[args.domain]
    corpus = ROOT / "_qa" / "cred"
    corpus = ROOT / "_qa" / "corpus" / "cred"

    rows = []
    for m in models:
        path = corpus / f"{prefix}_{m}.csv"
        n = 0
        with path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("text") or "").strip()
                if len(t) < 120:
                    continue
                e = extract(t, args.domain)
                if e["n_sentences"] < 8:
                    continue
                e["_ai"] = True
                rows.append(e)
                n += 1
                if args.sample and n >= args.sample:
                    break
    hu_path = corpus / f"{prefix}_human.csv"
    n = 0
    with hu_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            t = (row.get("text") or "").strip()
            if len(t) < 120:
                continue
            e = extract(t, args.domain)
            if e["n_sentences"] < 8:
                continue
            e["_ai"] = False
            rows.append(e)
            n += 1
            if args.sample and n >= args.sample:
                break

    ai = [r for r in rows if r["_ai"]]
    hu = [r for r in rows if not r["_ai"]]
    print(f"{args.domain}: 拟合样本 {len(rows)}（AI {len(ai)} / 真人 {len(hu)}）")

    # 单特征 AUROC 先打印，逐特征检查方向
    for feat in FEATS + ["conn_density"]:
        vals_ai = [r[feat] for r in rows if r["_ai"] and r[feat] == r[feat]]
        vals_hu = [r[feat] for r in rows if not r["_ai"] and r[feat] == r[feat]]
        if vals_ai and vals_hu:
            print(f"  {feat}: AUROC {auroc(vals_ai, vals_hu):.3f}")

    fit_feats = [f for f in FEATS if any(r[f] == r[f] for r in rows)]
    res = fit(rows, fit_feats, balance=True)
    coef, b = res
    auc = auroc([apply_model(r, fit_feats, coef, b) for r in ai],
                [apply_model(r, fit_feats, coef, b) for r in hu])
    hold = holdout(rows, fit_feats, balance=True)
    hu_scores = sorted(s for s in (apply_model(r, fit_feats, coef, b) for r in hu) if s == s)
    p50 = round(hu_scores[int(0.5 * (len(hu_scores) - 1))] * 100)
    p90 = round(hu_scores[int(0.9 * (len(hu_scores) - 1))] * 100)

    print(f"\n特征: {fit_feats}")
    print(f"全量 AUROC {auc:.3f} / 分层留出 {hold:.3f} / 真人 p50 {p50} p90 {p90}")
    print("\nscoring 段（粘贴进 YAML）：")
    print(f"corpus: \"C-ReD {args.domain} 域（过 8 句门槛样本：真人 {len(hu)} vs "
          f"{len(models)} 模型 {len(ai)}，类平衡加权），2026-09\"")
    print(f"intercept: {b:.4f}")
    for feat, c in zip(fit_feats, coef):
        print(f"{feat}: {c:.4f}")
    print(f"auroc: {round(auc, 3)}")
    print(f"auroc_holdout: {round(hold, 3)}")
    print(f"human_p50: {p50}")
    print(f"human_p90: {p90}")


if __name__ == "__main__":
    main()
