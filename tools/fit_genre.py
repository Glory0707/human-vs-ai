"""文种条件系数拟合与 YAML 产出（v0.20.0）。

判定线流程（tools/genre_check.py）说"落地文种系数"后跑本工具：
- 在该文种的全部配对语料上拟合（fit_score_tiers.fit，类平衡逻辑回归）；
- 分层 5 折 + 渠道留一 LOCO 复核（与 genre_check 同一实现，数字必须对得上）；
- auroc 字段取 LOCO（跨渠道外推是最保守的可信估计），锚点 human_p50/p90
  取文种真人全量在该系数下的分位；
- 终端打印可直接粘贴进 profile scoring 段 genre_scoring 的 YAML 块。

用法:python tools/fit_genre.py --genre approval-reply
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fit_score_tiers import FEATS, auroc, fit, apply_model  # noqa: E402
import genre_check as gc  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--genre", required=True, choices=sorted(gc.GENRE_NAME))
    args = ap.parse_args()
    kind = args.genre

    ai, hu = gc.load_rows()
    ai_k = [r for r in ai if r["feat"] and kind in r["kinds"]]
    hu_k = [r for r in hu if r["feat"] and kind in r["kinds"]]
    print(f"{gc.GENRE_NAME[kind]}（{kind}）：AI {len(ai_k)} / 真人 {len(hu_k)}")
    if len(ai_k) < 10 or len(hu_k) < 10:
        sys.exit("样本不足，不拟合")

    rows = [{**r["feat"], "_ai": True} for r in ai_k]
    rows += [{**r["feat"], "_ai": False} for r in hu_k]
    coef, intercept = fit(rows, FEATS)
    ai_z = [apply_model(r, FEATS, coef, intercept) for r in rows if r["_ai"]]
    hu_z = [apply_model(r, FEATS, coef, intercept) for r in rows if not r["_ai"]]

    auc_in = auroc(ai_z, hu_z)
    cv = gc.stratified_cv(ai_k, hu_k)
    loco = gc.loco_cv(ai_k, hu_k)
    p50 = gc.pct(hu_z, 0.5)
    p90 = gc.pct(hu_z, 0.9)
    z100 = [round(100 * z) for z in hu_z]

    print(f"\n全量拟合 in-sample AUROC {auc_in:.3f} · 分层 CV {cv:.3f} · LOCO {loco:.3f}")
    print(f"AI z 均值 {statistics.mean(ai_z):.3f} · 真人 z 均值 {statistics.mean(hu_z):.3f}")
    ok = cv >= gc.LINE and (loco is None or loco >= gc.LINE)
    print(f"判定线 {gc.LINE}：{'两道均过，可落地' if ok else '未全过——回退抑制，不要粘贴'}")

    print(f"""
  {kind}:
    corpus: "文种内校准：真人{gc.GENRE_NAME[kind]} {len(hu_k)} 篇（四省门户）vs AI {len(ai_k)} 篇（gen-oos+gen-cal），2026-09"
    intercept: {intercept:.4f}
    hit_density: {coef[FEATS.index('hit_density')]:.4f}
    sentence_cv: {coef[FEATS.index('sentence_cv')]:.4f}
    ttr: {coef[FEATS.index('ttr')]:.4f}
    ngram_repeat: {coef[FEATS.index('ngram_repeat')]:.4f}
    auroc: {min(x for x in (cv, loco) if x is not None):.3f}  # LOCO（跨渠道外推）
    human_p50: {p50}
    human_p90: {p90}
""")

    # 锚点分布供报告核对（真人指数应为 0-100 分位可读）
    print(f"真人指数分布（全量系数）：p50={p50} p90={p90} "
          f"min={min(z100)} max={max(z100)}")


if __name__ == "__main__":
    main()
