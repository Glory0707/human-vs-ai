"""general 评分最终拟合：4 特征 + 类平衡（HC3 91:300 失衡矫正）。

实验结论（_qa/exp_score_out.txt）：类平衡 full 0.838 / holdout 0.870，
不输现状（0.834/0.868），人类分位从 60/88 移到 27/69——截距不再被
训练集 24% 真人占比抬高，人味文本不再普遍偏高分。输出可粘贴 YAML。
从 _qa/exp_rows_general.json 读缓存特征，不再重新抽。
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from evaluate_cred import auroc  # noqa: E402

FEATURES = ["hit_density", "sentence_cv", "ttr", "ngram_repeat"]


def fit(rows, features, balance=True):
    data = []
    for r in rows:
        xs = [r[f] for f in features]
        if any(x != x for x in xs):
            continue
        data.append((xs, 1 if r["_ai"] else 0))
    n_f = len(features)
    n_ai = sum(y for _, y in data)
    n_hu = len(data) - n_ai
    w_neg = (n_ai / n_hu) if balance else 1.0
    means = [sum(row[i] for row, _ in data) / len(data) for i in range(n_f)]
    sds = []
    for i in range(n_f):
        v = sum((row[i] - means[i]) ** 2 for row, _ in data) / len(data)
        sds.append(math.sqrt(v) or 1.0)
    w = [0.0] * n_f
    b = 0.0
    lr = 0.5
    for it in range(4000):
        gw = [0.0] * n_f
        gb = 0.0
        for (row, y) in data:
            cw = 1.0 if y else w_neg
            z = b + sum(w[i] * (row[i] - means[i]) / sds[i] for i in range(n_f))
            p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
            e = (p - y) * cw
            gb += e
            for i in range(n_f):
                gw[i] += e * (row[i] - means[i]) / sds[i]
        m = sum((1.0 if y else w_neg) for _, y in data)
        for i in range(n_f):
            w[i] -= lr * (gw[i] / m + 1e-4 * w[i])
        b -= lr * gb / m
        if it == 1999:
            lr = 0.05
    coef = [w[i] / sds[i] for i in range(n_f)]
    intercept = b - sum(w[i] * means[i] / sds[i] for i in range(n_f))
    return coef, intercept


def apply_model(row, features, coef, intercept):
    z = intercept + sum(c * row[f] for c, f in zip(coef, features))
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


rows = json.loads((Path(__file__).parent / "exp_rows_general.json").read_text(encoding="utf-8"))
ai = [r for r in rows if r["_ai"]]
hu = [r for r in rows if not r["_ai"]]

coef, b = fit(rows, FEATURES, balance=True)
auc = auroc([apply_model(r, FEATURES, coef, b) for r in ai],
            [apply_model(r, FEATURES, coef, b) for r in hu])

rng = random.Random(7)
test_idx = set()
for cls in (True, False):
    idx = [i for i, r in enumerate(rows) if r["_ai"] == cls]
    rng.shuffle(idx)
    test_idx.update(idx[: len(idx) // 2])
train = [r for i, r in enumerate(rows) if i not in test_idx]
test = [r for i, r in enumerate(rows) if i in test_idx]
coef_t, b_t = fit(train, FEATURES, balance=True)
auc_holdout = auroc(
    [apply_model(r, FEATURES, coef_t, b_t) for r in test if r["_ai"]],
    [apply_model(r, FEATURES, coef_t, b_t) for r in test if not r["_ai"]])

hu_sorted = sorted(apply_model(r, FEATURES, coef, b) for r in hu)
p = lambda q: hu_sorted[int(q * (len(hu_sorted) - 1))]  # noqa: E731

print(f"full AUROC {auc:.3f}   holdout {auc_holdout:.3f}")
print(f"human p50 {p(0.5)*100:.0f}   p90 {p(0.9)*100:.0f}")
print()
print("scoring:")
print('  corpus: "HC3-Chinese 391 篇过 8 句门槛样本（真人 91 vs ChatGPT 300，类平衡加权），2026-09"')
print(f"  intercept: {b:.4f}")
for f, c in zip(FEATURES, coef):
    print(f"  {f}: {c:.4f}")
print(f"  auroc: {auc:.3f}")
print(f"  auroc_holdout: {auc_holdout:.3f}")
print(f"  human_p50: {round(p(0.5)*100)}")
print(f"  human_p90: {round(p(0.9)*100)}")
