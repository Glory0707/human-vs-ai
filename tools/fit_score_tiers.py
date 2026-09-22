"""按长度分档拟合 academic 评分系数（design.md §7 排队项：评分的短文摆放）。

与 fit_score.py 的区别：不做全局单模型，而是按 D-UNIF 同源的三档长度
（<300 / 300–600 / >600 字）分别拟合类平衡逻辑回归，并给出"每档系数 vs
全局系数"的对照。C-ReD paper 全量（不做 per_source 截断）；可选 --with-news
把 news 正文级语料并入长档（跨文体混入，结论仅作对照不直接采用）。

设计给服务器跑（全量抽取是小时级任务）：
    nohup python3 tools/fit_score_tiers.py --corpus _qa/corpus \
        --out _qa/score-fit-tiers > logs/fit-tiers.log 2>&1 &

产出：_qa/score-fit-tiers.json + 终端可粘贴的分层 scoring 提案。
某档样本不足（AI <30 或真人 <10）时该档标记 unreliable，建议沿用全局系数。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import engine  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

TIERS = [(0, 300, "短"), (300, 600, "中"), (600, None, "长")]
MIN_TIER_AI, MIN_TIER_HU = 30, 10
FEATS = ["hit_density", "sentence_cv", "ttr", "ngram_repeat"]


def iter_domain(corpus_dir: Path, domain: str):
    if domain == "paper":
        files = [(f"paper_{m}.csv", True) for m in
                 ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3")]
        files.append(("paper_human.csv", False))
    else:
        files = [("news_gpt-4o.csv", True), ("news_human.csv", False)]
    for fname, is_ai in files:
        path = corpus_dir / "cred" / fname
        with path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("text") or "").strip()
                if len(t) >= 120:
                    yield t, is_ai


def extract(text: str) -> dict:
    r = engine.analyze(text, "academic")
    st = r.doc_stats
    n = max(st.n_sentences, 1)
    ungated = sum(
        engine._SCORE_WEIGHT.get(f.severity, 1.0)
        for f in r.findings + r.hints
        if f.para >= 0
    )
    return {
        "n_chars": st.n_chars,
        "n_sentences": st.n_sentences,
        "hit_density": ungated / n,
        "sentence_cv": st.sentence_cv,
        "ttr": st.ttr,
        "ngram_repeat": st.ngram_repeat,
    }


def fit(rows, features, balance=True):
    """类平衡逻辑回归（标准化特征全量梯度下降）。

    有 numpy 时向量化（服务器共享 CPU 上快约两个量级），数学与
    纯 Python 版逐式一致：同一 lr 调度、同一 L2、同一类别加权。
    """
    data = []
    for r in rows:
        xs = [r[f] for f in features]
        if any(x != x for x in xs):
            continue
        data.append((xs, 1 if r["_ai"] else 0))
    if not data:
        return None
    n_f = len(features)
    n_ai = sum(y for _, y in data)
    n_hu = len(data) - n_ai
    w_neg = (n_ai / n_hu) if balance else 1.0

    if np is not None:
        X = np.array([row for row, _ in data], dtype=float)
        y = np.array([y for _, y in data], dtype=float)
        cw = np.where(y == 1, 1.0, w_neg)
        means = X.mean(axis=0)
        sds = np.sqrt(((X - means) ** 2).mean(axis=0))
        sds[sds == 0] = 1.0
        Xs = (X - means) / sds
        w = np.zeros(n_f)
        b = 0.0
        lr = 0.5
        m = cw.sum()
        for it in range(4000):
            z = np.clip(b + Xs @ w, -30, 30)
            p = 1 / (1 + np.exp(-z))
            e = (p - y) * cw
            w -= lr * ((Xs.T @ e) / m + 1e-4 * w)
            b -= lr * e.sum() / m
            if it == 1999:
                lr = 0.05
        coef = w / sds
        intercept = float(b - np.sum(w * means / sds))
        return [float(c) for c in coef], intercept

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
        for row, y in data:
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


def auroc(ai_scores, hu_scores):
    # 非有限分数先剔除：NaN 参与并列检测时 NaN==NaN 恒 False，j 不前进会死循环
    # （实证：<3 句摘要的 sentence_cv=NaN 过 apply_model 变 NaN 分数，首个分档即挂起）
    ai_scores = [s for s in ai_scores if s == s]
    hu_scores = [s for s in hu_scores if s == s]
    combined = [(s, 1) for s in ai_scores] + [(s, 0) for s in hu_scores]
    combined.sort(key=lambda x: x[0])
    ranks = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    rank_sum_ai = sum(r for r, (_, c) in zip(ranks, combined) if c == 1)
    n_ai = len(ai_scores)
    n_hu = len(hu_scores)
    if n_ai == 0 or n_hu == 0:
        return float("nan")
    # U1 = R1 - n1(n1+1)/2；AUC = U1/(n1*n2)——n1(n1+n2+1) 版少 +0.5 常数，
    # 会把所有 AUROC 压低 0.5（实证：真 0.953 被报成 0.453）
    return (rank_sum_ai - n_ai * (n_ai + 1) / 2) / (n_ai * n_hu)


def holdout(rows, features, balance=True, seed=7):
    rng = random.Random(seed)
    test_idx = set()
    for cls in (True, False):
        idx = [i for i, r in enumerate(rows) if r["_ai"] == cls]
        rng.shuffle(idx)
        test_idx.update(idx[: len(idx) // 2])
    train = [r for i, r in enumerate(rows) if i not in test_idx]
    test = [r for i, r in enumerate(rows) if i in test_idx]
    fitres = fit(train, features, balance)
    if fitres is None:
        return float("nan")
    coef, b = fitres
    ai = [apply_model(r, features, coef, b) for r in test if r["_ai"]]
    hu = [apply_model(r, features, coef, b) for r in test if not r["_ai"]]
    return auroc(ai, hu)


def eval_tier(rows, label):
    ai = [r for r in rows if r["_ai"]]
    hu = [r for r in rows if not r["_ai"]]
    print(f"[fit] {label}: n={len(rows)} (AI {len(ai)}/人 {len(hu)}) numpy={np is not None}", flush=True)
    out = {"tier": label, "n": len(rows), "n_ai": len(ai), "n_human": len(hu)}
    fitres = fit(rows, FEATS, balance=True)
    print(f"[fit] {label}: 拟合完成", flush=True)
    if fitres is None:
        out["reliable"] = False
        return out
    coef, b = fitres
    out["coef"] = {f: round(c, 4) for f, c in zip(FEATS, coef)}
    out["intercept"] = round(b, 4)
    out["auroc"] = round(auroc(
        [apply_model(r, FEATS, coef, b) for r in ai],
        [apply_model(r, FEATS, coef, b) for r in hu]), 3)
    out["holdout"] = round(holdout(rows, FEATS, balance=True), 3)
    hu_scores = sorted(s for s in (apply_model(r, FEATS, coef, b) for r in hu) if s == s)
    if not hu_scores:
        out["reliable"] = False
        return out
    out["human_p50"] = round(hu_scores[int(0.5 * (len(hu_scores) - 1))] * 100)
    out["human_p90"] = round(hu_scores[int(0.9 * (len(hu_scores) - 1))] * 100)
    out["reliable"] = len(ai) >= MIN_TIER_AI and len(hu) >= MIN_TIER_HU
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "_qa" / "corpus"))
    ap.add_argument("--out", default=str(ROOT / "_qa" / "score-fit-tiers.json"))
    ap.add_argument("--with-news", action="store_true", help="news 正文级语料并入长档对照")
    args = ap.parse_args()

    corpus_dir = Path(args.corpus)
    t0 = time.time()

    def extract_domain(domain):
        rows = []
        for text, is_ai in iter_domain(corpus_dir, domain):
            rows.append({**extract(text), "_ai": is_ai})
            if len(rows) % 50 == 0:
                print(f"[{domain}] {len(rows)} 篇，{time.time() - t0:.0f}s", flush=True)
        print(f"[{domain}] 抽取完成 {len(rows)} 篇，{time.time() - t0:.0f}s", flush=True)
        return rows

    rows_paper = extract_domain("paper")
    report = {"paper": None}
    variants = {"paper": rows_paper}
    if args.with_news:
        rows_news = extract_domain("news")
        variants["paper+news"] = rows_paper + rows_news

    for name, rows in variants.items():
        report[name] = {}
        tiers = []
        for lo, hi, label in TIERS:
            sub = [r for r in rows if r["n_chars"] >= lo and (hi is None or r["n_chars"] < hi)]
            res = eval_tier(sub, f"{label}({lo}-{hi if hi else '∞'}字)")
            print(f"[{name}] {res}", flush=True)
            tiers.append(res)
        # 全局单模型对照
        report[name]["tiers"] = tiers
        report[name]["global"] = eval_tier(rows, "全局")
        print(f"[{name}] 全局：auroc={report[name]['global'].get('auroc')} "
              f"holdout={report[name]['global'].get('holdout')}", flush=True)

    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {out}，总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
