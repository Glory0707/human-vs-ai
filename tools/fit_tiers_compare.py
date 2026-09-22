# -*- coding: utf-8 -*-
"""评分分档的最终对比：全局 vs 分档 × 平衡 vs 不平衡 × 现役系数，统一 holdout 口径。

v0.12.0 的决策依据（结论已落 academic.yaml 长档系数）：现役 245 系数 0.946 /
全局重拟合 0.948 / 长档（≥600 字）分档 0.975，短档分档反而更差故只落地长档。

特征读 _qa/_tier_rows_cache.json（paper 全量缓存，gitignore），缺失时自动
抽取重建（全量抽取小时级，建议在服务器跑）。运行：python tools/fit_tiers_compare.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.evaluate_cred import auroc  # noqa: E402
from tools.fit_score_tiers import FEATS, TIERS, apply_model, extract, fit, iter_domain  # noqa: E402

CACHE = ROOT / "_qa" / "_tier_rows_cache.json"


def load_rows() -> list[dict]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    rows = [{**extract(t), "_ai": ai} for t, ai in iter_domain(ROOT / "_qa" / "corpus", "paper")]
    CACHE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"特征缓存已重建 → {CACHE}（{len(rows)} 篇）")
    return rows


rows = load_rows()
deploy = [r for r in rows if r["n_sentences"] >= 8 and all(r[f] == r[f] for f in FEATS)]
print(f"部署口径（≥8 句、特征完整）: {len(deploy)} 篇")

DEPLOYED = {"hit_density": 8.9025, "sentence_cv": -14.8899, "ttr": 83.3770, "ngram_repeat": 22.6387}
DEP_B = -69.8327

rng = random.Random(7)
test_idx = set()
for cls in (True, False):
    idx = [i for i, r in enumerate(deploy) if r["_ai"] == cls]
    rng.shuffle(idx)
    test_idx.update(idx[: len(idx) // 2])
train = [r for i, r in enumerate(deploy) if i not in test_idx]
test = [r for i, r in enumerate(deploy) if i in test_idx]
print(f"train {len(train)} / test {len(test)}（分层半分）\n")


def score_set(rows_, coef, b):
    ai = [apply_model(r, FEATS, coef, b) for r in rows_ if r["_ai"]]
    hu = [apply_model(r, FEATS, coef, b) for r in rows_ if not r["_ai"]]
    return auroc(ai, hu)


# 现役系数：直接在 test 上评（它的拟合集是旧 245 子集，与本次 train 无重叠）
print(f"现役 245 系数          test AUROC {score_set(test, [DEPLOYED[f] for f in FEATS], DEP_B):.3f}")

for balance in (False, True):
    tag = "平衡" if balance else "不平衡"
    coef, b = fit(train, FEATS, balance=balance)
    print(f"全局重拟合({tag})         test AUROC {score_set(test, coef, b):.3f}")
    for lo, hi, label in TIERS:
        tr = [r for r in train if r["n_chars"] >= lo and (hi is None or r["n_chars"] < hi)]
        te = [r for r in test if r["n_chars"] >= lo and (hi is None or r["n_chars"] < hi)]
        if len(tr) < 40 or len(te) < 20:
            print(f"  {label}档: 样本不足 (train {len(tr)} / test {len(te)})")
            continue
        tcoef, tb = fit(tr, FEATS, balance=balance)
        coefs = dict(zip(FEATS, [round(c, 4) for c in tcoef]))
        print(f"  {label}档分档({tag})      test AUROC {score_set(te, tcoef, tb):.3f}"
              f"  (n_test {len(te)})  coef={coefs} int={round(tb, 4)}")
