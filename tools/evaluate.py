"""区分度评测：在真实人机语料上量化每条规则的区分能力。

数据：HC3-Chinese（Hello-SimpleAI，人类回答 vs ChatGPT 回答，2023 语料）。
注意：这是 2023 年的 ChatGPT，不是 2026 年的主流模型——数字用于规则
校准与回归基线，不用于宣传"准确率"（模型在迭代，规则库也按 era 演进，
见 docs/design.md）。

产出（_qa/eval-hc3.md + .json）：
- 总区分度：AI 与人类文本的句均命中数分布、按阈值的准确率/误报率
- 规则区分度表：每条规则在 AI/人类语料上的命中率 → 调阈值、砍废规则的依据
- 统计指标分布：句长 CV / TTR / 连接词密度的人类基线（校准 doc 规则阈值用）

用法：python tools/evaluate.py --per-domain 60 --out _qa/eval-hc3
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from human_vs_ai import engine  # noqa: E402

CORPUS_DIR = Path(__file__).parent.parent / "_qa" / "corpus"
DOMAINS = ["medicine", "baike"]


def load_samples(per_domain: int, min_len: int = 80, seed: int = 42) -> list[dict]:
    """每领域抽 per_domain 篇人类 + per_domain 篇 AI，过滤过短样本。"""
    rng = random.Random(seed)
    samples: list[dict] = []
    for domain in DOMAINS:
        path = CORPUS_DIR / f"{domain}.jsonl"
        if not path.exists():
            sys.exit(f"缺少语料 {path}——先下载 HC3-Chinese（见 docs/rules.md 校准章节）")
        humans, ais = [], []
        for line in path.open(encoding="utf-8"):
            item = json.loads(line)
            for a in item.get("human_answers", []):
                if len(a) >= min_len:
                    humans.append(a)
            for a in item.get("chatgpt_answers", []):
                if len(a) >= min_len:
                    ais.append(a)
        rng.shuffle(humans)
        rng.shuffle(ais)
        for a in humans[:per_domain]:
            samples.append({"text": a, "label": "human", "domain": domain})
        for a in ais[:per_domain]:
            samples.append({"text": a, "label": "ai", "domain": domain})
    return samples


def auroc(ai_scores: list[float], human_scores: list[float]) -> float:
    """秩和法 AUROC：AI 得分随机高于人类得分的概率。0.5=瞎猜，1.0=完美。"""
    combined = [(s, 1) for s in ai_scores] + [(s, 0) for s in human_scores]
    combined.sort(key=lambda x: x[0])
    ranks = {}
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2  # 1-based，并列取平均秩
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    rank_sum_ai = sum(ranks[k] for k, (_, lab) in enumerate(combined) if lab == 1)
    n_ai, n_h = len(ai_scores), len(human_scores)
    return (rank_sum_ai - n_ai * (n_ai + 1) / 2) / (n_ai * n_h)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-domain", type=int, default=60)
    ap.add_argument("--out", default="_qa/eval-hc3")
    args = ap.parse_args()

    samples = load_samples(args.per_domain)
    rules = engine.load_rules("academic")
    rule_ids = [r.id for r in rules if r.scope in ("sentence", "shape")]

    rows = []
    for s in samples:
        result = engine.analyze(s["text"], "academic")
        # low 规则孤立命中降级在 hints 里——评测要计入，否则弱规则的区分度被系统性漏算
        all_f = result.findings + result.hints
        sent_hits = sum(1 for f in all_f if f.para >= 0)
        n = max(result.doc_stats.n_sentences, 1)
        per_rule = {rid: 0 for rid in rule_ids}
        for f in all_f:
            if f.rule_id in per_rule:
                per_rule[f.rule_id] += 1
        rows.append(
            {
                "label": s["label"],
                "domain": s["domain"],
                "n_sentences": result.doc_stats.n_sentences,
                "hits_per_sent": sent_hits / n,
                "n_high": sum(1 for f in result.findings if f.severity == "high"),
                "cv": result.doc_stats.sentence_cv,
                "ttr": result.doc_stats.ttr,
                "conn": result.doc_stats.conn_density,
                "per_rule": per_rule,
            }
        )

    ai = [r for r in rows if r["label"] == "ai"]
    hu = [r for r in rows if r["label"] == "human"]

    def avg(rs, key):
        vals = [r[key] for r in rs if r[key] == r[key]]
        return sum(vals) / len(vals) if vals else float("nan")

    auc = auroc([r["hits_per_sent"] for r in ai], [r["hits_per_sent"] for r in hu])
    auc_cv = auroc(
        [-r["cv"] for r in ai if r["cv"] == r["cv"]],
        [-r["cv"] for r in hu if r["cv"] == r["cv"]],
    )  # CV 越低越像 AI，取负后统一"得分高=像AI"
    auc_ttr = auroc(
        [-r["ttr"] for r in ai if r["ttr"] == r["ttr"]],
        [-r["ttr"] for r in hu if r["ttr"] == r["ttr"]],
    )
    combined_score_auc = auroc(
        [
            -(r["cv"] if r["cv"] == r["cv"] else 0.45) + r["hits_per_sent"]
            for r in ai
        ],
        [
            -(r["cv"] if r["cv"] == r["cv"] else 0.45) + r["hits_per_sent"]
            for r in hu
        ],
    )

    lines = []
    lines.append("# HC3-Chinese 区分度评测")
    lines.append("")
    lines.append(f"样本：AI {len(ai)} 篇 / 人类 {len(hu)} 篇（medicine + baike，ChatGPT 2023 语料）")
    lines.append("")
    lines.append("## 总区分度")
    lines.append("")
    lines.append(f"- 句均命中数（词表规则）：AI {avg(ai, 'hits_per_sent'):.3f} vs 人类 {avg(hu, 'hits_per_sent'):.3f} · AUROC {auc:.3f}")
    lines.append(f"- 句长 CV 单指标 AUROC：{auc_cv:.3f}")
    lines.append(f"- TTR 单指标 AUROC：{auc_ttr:.3f}")
    lines.append(f"- CV+命中数组合 AUROC：{combined_score_auc:.3f}")
    lines.append(f"- 句长 CV 均值：AI {avg(ai, 'cv'):.3f} vs 人类 {avg(hu, 'cv'):.3f}")
    lines.append(f"- TTR 均值：AI {avg(ai, 'ttr'):.3f} vs 人类 {avg(hu, 'ttr'):.3f}")
    lines.append(f"- 连接词密度均值：AI {avg(ai, 'conn'):.3f} vs 人类 {avg(hu, 'conn'):.3f}")
    lines.append("")
    lines.append("## 阈值扫描（判定：句长 CV ≤ T 或 命中数 ≥ X 判为 AI）")
    lines.append("")
    lines.append("| 判据 | AI 召回 | 人类误报 | 准确率 |")
    lines.append("|---|---|---|---|")
    for t in [0.28, 0.32, 0.36, 0.40, 0.45]:
        tp = sum(1 for r in ai if r["cv"] == r["cv"] and r["cv"] <= t)
        fp = sum(1 for r in hu if r["cv"] == r["cv"] and r["cv"] <= t)
        acc = (tp + len(hu) - fp) / len(rows)
        lines.append(f"| CV ≤ {t} | {tp / len(ai):.2f} | {fp / len(hu):.2f} | {acc:.2f} |")
    lines.append("")
    lines.append("## 规则区分度（命中率 = 含该规则命中的文本比例）")
    lines.append("")
    lines.append("| 规则 | AI 命中率 | 人类命中率 | 区分度 |")
    lines.append("|---|---|---|---|")
    for rid in rule_ids:
        ai_rate = sum(1 for r in ai if r["per_rule"][rid] > 0) / len(ai)
        hu_rate = sum(1 for r in hu if r["per_rule"][rid] > 0) / len(hu)
        flag = " ← 考虑删除/降级" if ai_rate > 0 and hu_rate > ai_rate else ""
        lines.append(f"| {rid} | {ai_rate:.2f} | {hu_rate:.2f} | {ai_rate - hu_rate:+.2f}{flag} |")
    lines.append("")

    out_md = Path(args.out + ".md")
    out_json = Path(args.out + ".json")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")
    out_json.write_text(
        json.dumps(
            {
                "auroc": auc,
                "ai_mean_hits": avg(ai, "hits_per_sent"),
                "human_mean_hits": avg(hu, "hits_per_sent"),
                "ai_cv": avg(ai, "cv"),
                "human_cv": avg(hu, "cv"),
                "ai_ttr": avg(ai, "ttr"),
                "human_ttr": avg(hu, "ttr"),
                "ai_conn": avg(ai, "conn"),
                "human_conn": avg(hu, "conn"),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print("\n".join(lines))
    print(f"\n已写入 {out_md} 与 {out_json}")


if __name__ == "__main__":
    main()
