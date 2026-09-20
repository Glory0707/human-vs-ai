"""综合评分拟合 + 分层消融：验证"综合 > 扣词"并产出评分模型系数。

用户问题：现在是综合判断还是碰词就判？本脚本用同一批语料跑四臂消融：
  A 扣词（无门控）   ：所有规则命中（含弱命中）按严重级加权求密度——"碰到就算"
  B 门控规则（现状） ：共现门控后的正式发现加权密度（low 单次命中不算）
  C 纯统计层        ：只看全文统计（cv/ttr/ngram/conn 的逻辑回归）
  D 综合评分        ：门控规则密度 + 全文统计 一起进逻辑回归

然后 D 的系数落进 profile YAML 的 scoring 段（校准只改数据的纪律），
引擎据此给每篇出 0-100 的 AI 味指数。分层拟合（半样本拟合、另半验证）。

用法：python tools/fit_score.py
产出：_qa/score-fit.md + .json；终端打印可粘贴的 YAML scoring 段。
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from human_vs_ai import engine  # noqa: E402
from evaluate_cred import load_samples as load_cred, auroc  # noqa: E402
from evaluate import load_samples as load_hc3  # noqa: E402

ROOT = Path(__file__).parent.parent
MIN_SENTS = 8  # 与引擎出分门槛同口径
WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}

# 两个 profile 的语料与特征集：特征必须是该 profile 上方向已验证的。
# 规则特征用未门控加权密度——门控是"逐句指控"的纪律，文档级聚合保留幅度
# 信息更有效（消融实测 0.847 vs 0.810）；general 不进 conn_density（反向）。
BENCHES = {
    "academic": {
        "load": lambda: load_cred(80),
        "ai": lambda s: s["source"] != "human",
        "features": ["hit_density", "sentence_cv", "ttr", "ngram_repeat"],
        "corpus": "C-ReD paper 400 篇（真人 80 vs deepseek-v3/qwen-3/gpt-4o/deepseek-r1 各 80）",
    },
    "general": {
        "load": lambda: load_hc3(500),
        "ai": lambda s: s["label"] == "ai",
        "features": ["hit_density", "sentence_cv", "ttr", "ngram_repeat"],
        "corpus": "HC3-Chinese（medicine+baike 人类/ChatGPT，2023 语料，过 8 句门槛者）",
    },
}


def extract(text: str, profile: str) -> dict:
    r = engine.analyze(text, profile)
    st = r.doc_stats
    n = max(st.n_sentences, 1)
    gated = sum(engine._SCORE_WEIGHT.get(f.severity, 1.0) for f in r.findings if f.para >= 0)
    ungated = sum(engine._SCORE_WEIGHT.get(f.severity, 1.0) for f in r.findings + r.hints if f.para >= 0)
    return {
        "n_sentences": st.n_sentences,
        "gated_density": gated / n,
        "hit_density": ungated / n,
        "sentence_cv": st.sentence_cv,
        "ttr": st.ttr,  # 字级 2-gram 口径（v0.11.0 起与评分/JS 端同口径）
        "ngram_repeat": st.ngram_repeat,
        "conn_density": st.conn_density,
    }


def fit_logistic(rows, features, seed=42):
    """纯 Python 逻辑回归（标准化特征上梯度下降，系数换算回原始量纲）。"""
    data = []
    for r in rows:
        xs = [r[f] for f in features]
        if any(x != x for x in xs):
            continue
        data.append((xs, 1 if r["_ai"] else 0))
    n_f = len(features)
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
            z = b + sum(w[i] * (row[i] - means[i]) / sds[i] for i in range(n_f))
            p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
            e = p - y
            gb += e
            for i in range(n_f):
                gw[i] += e * (row[i] - means[i]) / sds[i]
        m = len(data)
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


def main() -> None:
    rng = random.Random(42)
    report = {}
    for profile, cfg in BENCHES.items():
        samples = cfg["load"]()
        rows = []
        for s in samples:
            feats = extract(s["text"], profile)
            if feats["n_sentences"] < MIN_SENTS:  # 与出分门槛同口径，短文不参拟合
                continue
            feats["_ai"] = cfg["ai"](s)
            rows.append(feats)
        ai = [r for r in rows if r["_ai"]]
        hu = [r for r in rows if not r["_ai"]]

        # ---- 四臂消融 ----
        auc_ungated = auroc([r["hit_density"] for r in ai], [r["hit_density"] for r in hu])
        auc_gated = auroc([r["gated_density"] for r in ai], [r["gated_density"] for r in hu])
        feats_c = [f for f in cfg["features"] if f != "hit_density"]
        coef_c, int_c = fit_logistic(rows, feats_c)
        auc_stats = auroc(
            [apply_model(r, feats_c, coef_c, int_c) for r in ai],
            [apply_model(r, feats_c, coef_c, int_c) for r in hu])
        feats_d = cfg["features"]
        coef_d, int_d = fit_logistic(rows, feats_d)
        score = lambda r: apply_model(r, feats_d, coef_d, int_d)  # noqa: E731
        auc_full = auroc([score(r) for r in ai], [score(r) for r in hu])

        # ---- 分层验证：半拟合半验证（按类别分层抽样）----
        rng2 = random.Random(7)
        test_idx = set()
        for cls in (True, False):
            idx = [i for i, r in enumerate(rows) if r["_ai"] == cls]
            rng2.shuffle(idx)
            test_idx.update(idx[: len(idx) // 2])
        train = [r for i, r in enumerate(rows) if i not in test_idx]
        test = [r for i, r in enumerate(rows) if i in test_idx]
        coef_t, int_t = fit_logistic(train, feats_d)
        auc_holdout = auroc(
            [apply_model(r, feats_d, coef_t, int_t) for r in test if r["_ai"]],
            [apply_model(r, feats_d, coef_t, int_t) for r in test if not r["_ai"]])

        # 人类基线分位（阈值带参考）
        hs = sorted(apply_model(r, feats_d, coef_d, int_d) for r in hu)
        p = lambda q: hs[int(q * (len(hs) - 1))]  # noqa: E731

        report[profile] = {
            "n": len(rows), "n_ai": len(ai), "n_human": len(hu),
            "auroc": {"ungated_rules": round(auc_ungated, 3), "gated_rules": round(auc_gated, 3),
                      "stats_only": round(auc_stats, 3), "full": round(auc_full, 3),
                      "full_holdout": round(auc_holdout, 3)},
            "human_p50_p90": (round(p(0.5) * 100), round(p(0.9) * 100)),
            "features": feats_d,
            "coef": [round(c, 4) for c in coef_d], "intercept": round(int_d, 4),
        }
        print(f"\n## {profile}（{len(rows)} 篇：AI {len(ai)} / 真人 {len(hu)}）")
        print(f"A 扣词（无门控）      AUROC {auc_ungated:.3f}")
        print(f"B 门控规则（现状）    AUROC {auc_gated:.3f}")
        print(f"C 纯统计层            AUROC {auc_stats:.3f}")
        print(f"D 综合（B+C 逻辑回归） AUROC {auc_full:.3f}（分层留出 {auc_holdout:.3f}）")
        print(f"人类指数 p50/p90 = {p(0.5)*100:.0f} / {p(0.9)*100:.0f}")
        print("scoring 段：")
        print(f"scoring:")
        print(f"  corpus: \"{cfg['corpus']}\"")
        print(f"  intercept: {int_d:.4f}")
        for f, c in zip(feats_d, coef_d):
            print(f"  {f}: {c:.4f}")
        print(f"  # 全量拟合 AUROC {auc_full:.3f}，分层留出 {auc_holdout:.3f}")

    out = ROOT / "_qa" / "score-fit"
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {out}.json")


if __name__ == "__main__":
    main()
