# -*- coding: utf-8 -*-
"""分档拟合的诊断：单特征 AUROC / 部署口径(≥8句)对照 / numpy vs 纯 Python 拟合。

背景：全量 C-ReD 上分档/全局拟合 AUROC=0.417（<0.5，方向反转），而 245 子集
上同特征集 0.984。本脚本不拟合出系数，只回答三个问题：
  1) 全量语料上各单特征的 AUROC（基准真相，不受拟合影响）
  2) 部署口径（≥8 句，与引擎出分门槛一致）上同样的单特征 AUROC
  3) 同一子样本上 numpy 拟合与纯 Python 拟合的系数是否一致（排除实现 bug）
运行（服务器）：python3 tools/fit_tiers_diag.py --corpus _qa/corpus
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.fit_score_tiers import FEATS, extract, fit, iter_domain  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
from evaluate_cred import auroc  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "_qa" / "corpus"))
    ap.add_argument("--sub", type=int, default=1200, help="拟合对照的子样本量")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    rows = []
    for text, is_ai in iter_domain(corpus, "paper"):
        rows.append({**extract(text), "_ai": is_ai})
    print(f"抽取 {len(rows)} 篇")

    import random
    rng = random.Random(1)
    sub = rows[:]
    rng.shuffle(sub)
    sub = sub[: args.sub]

    def feat_auroc(rows_, feat):
        ai = [r[feat] for r in rows_ if r["_ai"] and r[feat] == r[feat]]
        hu = [r[feat] for r in rows_ if not r["_ai"] and r[feat] == r[feat]]
        return round(auroc(ai, hu), 3) if ai and hu else None

    def report_pop(rows_, name):
        n8 = sum(1 for r in rows_ if r["n_sentences"] >= 8)
        print(f"\n== {name}: {len(rows_)} 篇（≥8 句 {n8}）==", flush=True)
        for f in FEATS:
            print(f"  {f}: 全量 AUROC {feat_auroc(rows_, f)}  |  ≥8 句 AUROC {feat_auroc([r for r in rows_ if r['n_sentences'] >= 8], f)}", flush=True)

    report_pop(rows, "paper 全量")

    # 部署口径特征矩阵上的 numpy vs 纯 Python 拟合对照
    deploy = [r for r in sub if r["n_sentences"] >= 8 and all(
        r[f] == r[f] for f in FEATS)]
    print(f"\n拟合对照子样本（部署口径完整特征）: {len(deploy)} 篇")
    import fit_score_tiers as fst
    coef_np, b_np = fst.fit(deploy, FEATS, balance=True)
    print(f"numpy   coef={['%.3f' % c for c in coef_np]} int={b_np:.3f}")
    saved = fst.np
    fst.np = None  # 强制纯 Python 路径
    coef_py, b_py = fst.fit(deploy, FEATS, balance=True)
    fst.np = saved
    print(f"purepy  coef={['%.3f' % c for c in coef_py]} int={b_py:.3f}")
    import math
    same = all(abs(a - b) < 1e-6 for a, b in zip(coef_np, coef_py)) and abs(b_np - b_py) < 1e-6
    print(f"两实现一致: {same}")

    # 部署口径上：现役 academic 系数的 AUROC（用 evaluate 的 245 子集拟合值）
    deployed = {"hit_density": 8.9025, "sentence_cv": -14.8899, "ttr": 83.3770, "ngram_repeat": 22.6387}
    dep_b = -69.8327
    ai = [sum(deployed[f] * r[f] for f in FEATS) + dep_b for r in deploy if r["_ai"]]
    hu = [sum(deployed[f] * r[f] for f in FEATS) + dep_b for r in deploy if not r["_ai"]]
    print(f"现役 245 系数在部署口径子样本上的 AUROC: {auroc(ai, hu):.3f}")

    # 保存缓存供后续分析
    out = Path("_qa/_tier_rows_cache.json")
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"特征缓存 → {out}")


if __name__ == "__main__":
    main()
