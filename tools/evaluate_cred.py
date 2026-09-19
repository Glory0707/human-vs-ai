"""学术场景区分度评测：C-ReD paper 域（真实论文摘要，9 模型 + 真人）。

与 evaluate.py（HC3 问答文体）互补：这里评的是学术 profile 的主场——
论文摘要正是知网 AIGC 检测的主战场。C-ReD 覆盖 2025-2026 主流模型
（含推理模型 deepseek-r1，已知最难检的一类）。

产出：_qa/eval-cred.md（区分度+规则校准表）+ .json
用法：python tools/evaluate_cred.py --per-source 80
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from human_vs_ai import engine  # noqa: E402

CORPUS_DIR = Path(__file__).parent.parent / "_qa" / "corpus" / "cred"
SOURCES = ["human", "deepseek-v3", "qwen-3", "gpt-4o", "deepseek-r1"]


def load_samples(per_source: int, min_len: int = 120, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    samples: list[dict] = []
    for src in SOURCES:
        path = CORPUS_DIR / f"paper_{src}.csv"
        if not path.exists():
            sys.exit(f"缺少语料 {path}（下载见 docs/rules.md 校准章节）")
        texts = []
        with path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("text") or "").strip()
                if len(t) >= min_len:
                    texts.append(t)
        rng.shuffle(texts)
        for t in texts[:per_source]:
            samples.append({"text": t, "source": src})
    return samples


def auroc(ai_scores: list[float], human_scores: list[float]) -> float:
    combined = [(s, 1) for s in ai_scores] + [(s, 0) for s in human_scores]
    combined.sort(key=lambda x: x[0])
    ranks: list[float] = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    rank_sum_ai = sum(r for r, (_, lab) in zip(ranks, combined) if lab == 1)
    n_ai, n_h = len(ai_scores), len(human_scores)
    return (rank_sum_ai - n_ai * (n_ai + 1) / 2) / (n_ai * n_h)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-source", type=int, default=80)
    ap.add_argument("--out", default="_qa/eval-cred")
    args = ap.parse_args()

    samples = load_samples(args.per_source)
    rules = engine.load_rules("academic")
    rule_ids = [r.id for r in rules if r.scope in ("sentence", "shape")]

    rows = []
    for s in samples:
        result = engine.analyze(s["text"], "academic")
        all_f = result.findings + result.hints
        n = max(result.doc_stats.n_sentences, 1)
        per_rule = {rid: 0 for rid in rule_ids}
        for f in all_f:
            if f.rule_id in per_rule:
                per_rule[f.rule_id] += 1
        rows.append(
            {
                "source": s["source"],
                "is_ai": s["source"] != "human",
                "n_sentences": result.doc_stats.n_sentences,
                "hits_per_sent": sum(1 for f in all_f if f.para >= 0) / n,
                "cv": result.doc_stats.sentence_cv,
                "ttr": result.doc_stats.ttr,
                "conn": result.doc_stats.conn_density,
                "per_rule": per_rule,
            }
        )

    hu = [r for r in rows if not r["is_ai"]]
    ai = [r for r in rows if r["is_ai"]]

    def avg(rs, key):
        vals = [r[key] for r in rs if r[key] == r[key]]
        return sum(vals) / len(vals) if vals else float("nan")

    lines = ["# C-ReD paper 域区分度评测（学术 profile 主场）", ""]
    lines.append(
        f"样本：真人 {len(hu)} 篇 vs AI {len(ai)} 篇"
        f"（deepseek-v3 / qwen-3 / gpt-4o / deepseek-r1 各 {args.per_source}，真实论文摘要）"
    )
    lines.append("")
    lines.append("## 分模型统计（均值）")
    lines.append("")
    lines.append("| 来源 | 句均命中 | 句长CV | TTR | 连接词密度 |")
    lines.append("|---|---|---|---|---|")
    for src in SOURCES:
        rs = [r for r in rows if r["source"] == src]
        lines.append(
            f"| {src} | {avg(rs, 'hits_per_sent'):.3f} | {avg(rs, 'cv'):.3f} | {avg(rs, 'ttr'):.3f} | {avg(rs, 'conn'):.3f} |"
        )
    lines.append("")
    auc_hit = auroc([r["hits_per_sent"] for r in ai], [r["hits_per_sent"] for r in hu])
    auc_cv = auroc(
        [-r["cv"] for r in ai if r["cv"] == r["cv"]],
        [-r["cv"] for r in hu if r["cv"] == r["cv"]],
    )
    auc_ttr = auroc(
        [-r["ttr"] for r in ai if r["ttr"] == r["ttr"]],
        [-r["ttr"] for r in hu if r["ttr"] == r["ttr"]],
    )
    lines.append("## 总区分度（真人 vs 全部模型）")
    lines.append("")
    lines.append(f"- 词表规则句均命中 AUROC：{auc_hit:.3f}")
    lines.append(f"- 句长 CV 单指标 AUROC：{auc_cv:.3f}")
    lines.append(f"- TTR 单指标 AUROC：{auc_ttr:.3f}")
    lines.append(f"- 句长 CV：真人 {avg(hu, 'cv'):.3f} vs AI {avg(ai, 'cv'):.3f}")
    lines.append(f"- TTR：真人 {avg(hu, 'ttr'):.3f} vs AI {avg(ai, 'ttr'):.3f}")
    lines.append(f"- 连接词密度：真人 {avg(hu, 'conn'):.3f} vs AI {avg(ai, 'conn'):.3f}")
    lines.append("")
    lines.append("## 阈值扫描（判定：CV ≤ T）")
    lines.append("")
    lines.append("| T | AI 召回 | 真人误报 | 准确率 |")
    lines.append("|---|---|---|---|")
    for t in [0.28, 0.32, 0.36, 0.40, 0.45]:
        tp = sum(1 for r in ai if r["cv"] == r["cv"] and r["cv"] <= t)
        fp = sum(1 for r in hu if r["cv"] == r["cv"] and r["cv"] <= t)
        lines.append(
            f"| {t} | {tp / len(ai):.2f} | {fp / len(hu):.2f} | {(tp + len(hu) - fp) / len(rows):.2f} |"
        )
    lines.append("")
    lines.append("## 规则区分度（命中率 = 含命中的文本比例；含弱命中）")
    lines.append("")
    lines.append("| 规则 | AI 命中率 | 真人命中率 | 区分度 |")
    lines.append("|---|---|---|---|")
    for rid in rule_ids:
        ai_rate = sum(1 for r in ai if r["per_rule"][rid] > 0) / len(ai)
        hu_rate = sum(1 for r in hu if r["per_rule"][rid] > 0) / len(hu)
        flag = " ← 校准" if hu_rate > ai_rate and ai_rate < 0.05 else ""
        lines.append(f"| {rid} | {ai_rate:.2f} | {hu_rate:.2f} | {ai_rate - hu_rate:+.2f}{flag} |")
    lines.append("")

    out_md = Path(args.out + ".md")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")
    Path(args.out + ".json").write_text(
        json.dumps({"rows": rows, "auroc": {"hits": auc_hit, "cv": auc_cv, "ttr": auc_ttr}}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("\n".join(lines))
    print(f"\n已写入 {out_md}")


if __name__ == "__main__":
    main()
