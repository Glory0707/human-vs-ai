"""实验：general 评分能否更好？加特征 / 类平衡 / 去掉表现差的特征。

跑通后有用结论才回填 fit_score.py 与 YAML。一次性实验脚本，不进包。
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from human_vs_ai import engine, segment, stats  # noqa: E402
from evaluate_cred import load_samples as load_cred, auroc  # noqa: E402
from evaluate import load_samples as load_hc3  # noqa: E402

WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}


def extract(text: str, profile: str) -> dict:
    r = engine.analyze(text, profile)
    st = r.doc_stats
    n = max(st.n_sentences, 1)
    ungated = sum(engine._SCORE_WEIGHT.get(f.severity, 1.0) for f in r.findings + r.hints if f.para >= 0)
    full_raw = "".join(s.text for block in segment.split_document(text) for s in block.sents)
    return {
        "n_sentences": st.n_sentences,
        "hit_density": ungated / n,
        "sentence_cv": st.sentence_cv,
        "ttr": stats.mattr(stats.tokenize_2gram(full_raw)),
        "ngram_repeat": st.ngram_repeat,
        "para_len_cv": st.para_len_cv,
        "dash_density": st.dash_density,
    }


def fit(rows, features, seed=42, balance=False):
    data = []
    for r in rows:
        xs = [r[f] for f in features]
        if any(x != x for x in xs):
            continue
        data.append((xs, 1 if r["_ai"] else 0))
    n_f = len(features)
    n_ai = sum(y for _, y in data)
    n_hu = len(data) - n_ai
    # 类平衡：人类样本按 AI:真人 比例加权（负类权重 > 1）
    w_pos = 1.0
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
            cw = w_pos if y else w_neg
            z = b + sum(w[i] * (row[i] - means[i]) / sds[i] for i in range(n_f))
            p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
            e = (p - y) * cw
            gb += e
            for i in range(n_f):
                gw[i] += e * (row[i] - means[i]) / sds[i]
        m = sum((w_pos if y else w_neg) for _, y in data)
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


def holdout_auc(rows, features, balance):
    rng = random.Random(7)
    test_idx = set()
    for cls in (True, False):
        idx = [i for i, r in enumerate(rows) if r["_ai"] == cls]
        rng.shuffle(idx)
        test_idx.update(idx[: len(idx) // 2])
    train = [r for i, r in enumerate(rows) if i not in test_idx]
    test = [r for i, r in enumerate(rows) if i in test_idx]
    coef, b = fit(train, features, balance=balance)
    ai = [apply_model(r, features, coef, b) for r in test if r["_ai"]]
    hu = [apply_model(r, features, coef, b) for r in test if not r["_ai"]]
    return auroc(ai, hu)


CUR = ["hit_density", "sentence_cv", "ttr", "ngram_repeat"]
VARIANTS = {
    "现状4特征": (CUR, False),
    "现状+类平衡": (CUR, True),
    "6特征(+段CV+破折号)": (CUR + ["para_len_cv", "dash_density"], False),
    "6特征+类平衡": (CUR + ["para_len_cv", "dash_density"], True),
    "6特征去句CV+类平衡": (["hit_density", "ttr", "ngram_repeat", "para_len_cv", "dash_density"], True),
}

for profile, load, is_ai in [
    ("general", lambda: load_hc3(500), lambda s: s["label"] == "ai"),
    # academic 0.984 已饱和且抽特征太慢（400 篇长摘要），本轮不动
]:
    cache = Path(__file__).parent / f"exp_rows_{profile}.json"
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        samples = load()
        rows = []
        for k, s in enumerate(samples):
            f = extract(s["text"], profile)
            if f["n_sentences"] < 8:
                continue
            f["_ai"] = is_ai(s)
            rows.append(f)
            if k % 50 == 0:
                print(f"[{profile}] extracted {k}/{len(samples)}", flush=True)
        cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"\n===== {profile}（{len(rows)} 篇：AI {sum(r['_ai'] for r in rows)} / 真人 {sum(not r['_ai'] for r in rows)}）", flush=True)
    for name, (feats, bal) in VARIANTS.items():
        coef, b = fit(rows, feats, balance=bal)
        auc = auroc([apply_model(r, feats, coef, b) for r in rows if r["_ai"]],
                    [apply_model(r, feats, coef, b) for r in rows if not r["_ai"]])
        ho = holdout_auc(rows, feats, bal)
        hu_sorted = sorted(apply_model(r, feats, coef, b) for r in rows if not r["_ai"])
        p50 = hu_sorted[int(0.5 * (len(hu_sorted) - 1))] * 100
        p90 = hu_sorted[int(0.9 * (len(hu_sorted) - 1))] * 100
        print(f"{name:24s} full {auc:.3f}  holdout {ho:.3f}  human p50/p90 = {p50:.0f}/{p90:.0f}  coef={['%.2f'%c for c in coef]} int={b:.2f}")
