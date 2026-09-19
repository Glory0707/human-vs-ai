"""T4.3 统计阈值分档分析：文本长度如何影响各统计指标的人机区分度。

动机：现有 D-UNIF/D-CONN 阈值在 paper 摘要语料（短文本）上标定，
完整论文正文（长文本）的基线可能不同——摘要句短而密，正文句长而疏。
本脚本把 paper（摘要级）与 news（正文级）两域合并，按长度分档，
每档分别统计真人/AI 的指标分布与 AUROC，回答三个问题：
1. 句长 CV 的真人基线随长度怎么变？（决定是否分档、每档阈值）
2. 连接词密度的差距在长文本上是放大还是缩小？
3. 哪些指标在长文本上更稳（正文级报告应突出哪些）？

产出：_qa/length-tiers.md（含建议阈值）+ .json
用法：python tools/analyze_length_tiers.py
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from human_vs_ai import engine  # noqa: E402

CORPUS = Path(__file__).parent.parent / "_qa" / "corpus" / "cred"
TIERS = [
    ("短（<300 字，摘要级）", lambda n: n < 300),
    ("中（300–600 字）", lambda n: 300 <= n <= 600),
    ("长（>600 字，正文级）", lambda n: n > 600),
]


def load(domain: str, sources: list[str], per_source: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for src in sources:
        path = CORPUS / domain / f"{domain}_{src}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if (r.get("text") or "").strip()]
        rng.shuffle(rows)
        for r in rows[:per_source]:
            out.append({"text": r["text"].strip(), "source": src, "is_ai": src != "human"})
    return out


def quantile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(int(len(xs) * q), len(xs) - 1)]


def main() -> None:
    per = 400
    paper = load("paper", ["human", "gpt-4o", "deepseek-v3", "qwen-3"], per)
    news = load("news", ["human", "gpt-4o"], per)
    rows = []
    for s in paper + news:
        res = engine.analyze(s["text"], "academic")
        st = res.doc_stats
        rows.append(
            {
                "domain": "paper" if s in paper else "news",
                "is_ai": s["is_ai"],
                "chars": st.n_chars,
                "cv": st.sentence_cv,
                "conn": st.conn_density,
                "ngram": st.ngram_repeat,
                "dash": st.dash_density,
            }
        )

    def auroc(ai_s: list[float], hu_s: list[float]) -> float:
        pair = [(v, 1) for v in ai_s if v == v] + [(v, 0) for v in hu_s if v == v]
        if not pair or not any(l == 1 for _, l in pair) or not any(l == 0 for _, l in pair):
            return float("nan")
        pair.sort(key=lambda x: x[0])
        ranks = [0.0] * len(pair)
        i = 0
        while i < len(pair):
            j = i
            while j < len(pair) and pair[j][0] == pair[i][0]:
                j += 1
            for k in range(i, j):
                ranks[k] = (i + j + 1) / 2
            i = j
        rai = sum(r for r, (_, lab) in zip(ranks, pair) if lab == 1)
        n_ai = sum(1 for _, l in pair if l == 1)
        n_h = len(pair) - n_ai
        return (rai - n_ai * (n_ai + 1) / 2) / (n_ai * n_h)

    lines = ["# 统计阈值分档分析（T4.3）", ""]
    lines.append("语料：C-ReD paper（摘要级）+ news（正文级）；AI = gpt-4o/deepseek-v3/qwen-3")
    lines.append("")
    lines.append("| 长度档 | n(AI/人) | CV 中位(AI) | CV 中位(人) | CV AUROC | 连接词中位(AI) | 连接词中位(人) | conn AUROC |")
    lines.append("|---|---|---|---|---|---|---|---|")
    tier_rows = []
    for name, pred in TIERS:
        sub = [r for r in rows if pred(r["chars"])]
        ai = [r for r in sub if r["is_ai"]]
        hu = [r for r in sub if not r["is_ai"]]
        if not ai or not hu:
            continue
        cv_ai = quantile([r["cv"] for r in ai if r["cv"] == r["cv"]], 0.5)
        cv_hu = quantile([r["cv"] for r in hu if r["cv"] == r["cv"]], 0.5)
        conn_ai = quantile([r["conn"] for r in ai if r["conn"] == r["conn"]], 0.5)
        conn_hu = quantile([r["conn"] for r in hu if r["conn"] == r["conn"]], 0.5)
        auc_cv = auroc(
            [-r["cv"] for r in ai if r["cv"] == r["cv"]],
            [-r["cv"] for r in hu if r["cv"] == r["cv"]],
        )
        auc_conn = auroc([r["conn"] for r in ai if r["conn"] == r["conn"]],
                         [r["conn"] for r in hu if r["conn"] == r["conn"]])
        tier_rows.append(
            {
                "tier": name,
                "n_ai": len(ai),
                "n_hu": len(hu),
                "cv_ai_median": cv_ai,
                "cv_hu_median": cv_hu,
                "cv_hu_p10": quantile([r["cv"] for r in hu if r["cv"] == r["cv"]], 0.10),
                "cv_hu_p25": quantile([r["cv"] for r in hu if r["cv"] == r["cv"]], 0.25),
                "conn_ai_median": conn_ai,
                "conn_hu_median": conn_hu,
                "conn_hu_p90": quantile([r["conn"] for r in hu if r["conn"] == r["conn"]], 0.90),
                "auc_cv": auc_cv,
                "auc_conn": auc_conn,
            }
        )
        lines.append(
            f"| {name} | {len(ai)}/{len(hu)} | {cv_ai:.3f} | {cv_hu:.3f} | {auc_cv:.3f} | {conn_ai:.3f} | {conn_hu:.3f} | {auc_conn:.3f} |"
        )

    lines.append("")
    lines.append("## 真人 CV 分位（分档阈值依据）")
    lines.append("")
    lines.append("| 长度档 | 真人 CV p10 | p25 | p50 |")
    lines.append("|---|---|---|---|")
    for tr in tier_rows:
        lines.append(
            f"| {tr['tier']} | {tr['cv_hu_p10']:.3f} | {tr['cv_hu_p25']:.3f} | {tr['cv_hu_median']:.3f} |"
        )
    lines.append("")
    lines.append("## 结论（回写 rules YAML 的依据，见 docs/rules.md §5）")

    out = Path("_qa/length-tiers.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    Path("_qa/length-tiers.json").write_text(
        json.dumps({"tiers": tier_rows}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("\n".join(lines))
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
