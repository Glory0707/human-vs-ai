"""多文体侦察：新语料域上现有词表/统计底盘的区分度盘点（校准前置事实）。

对 C-ReD 的每个域（composition/news/review/question answer/paper）× 每个
现有 profile 词表，量三件事：
  1) 样本形态：篇数、过 8 句门槛比例、字数/句数分布（评分可行性）
  2) 单特征 AUROC：未门控命中密度 + 三个统计特征（评分底盘可用性）
  3) 逐规则区分度：每条规则在该域的 真人/AI 命中率 与 AUROC（词表选留）

只读语料产出 Markdown 报告，不改任何规则——先量事实，再动手校准
（消融先于建模的纪律）。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import engine, stats as hvastats  # noqa: E402
from tools.fit_score_tiers import auroc  # noqa: E402

# C-ReD 域 → (目录前缀, 生成器列表)。生成器取当代主力四款 + doubao/claude 补广度。
DOMAINS = {
    "composition": ("composition", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                                    "doubao-1.5-pro", "claude-3.5-haiku", "gemini-2.5-flash",
                                    "gpt-3.5-turbo", "qwen-2.5")),
    "news": ("news", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                      "doubao-1.5-pro", "claude-3.5-haiku", "gemini-2.5-flash",
                      "gpt-3.5-turbo", "qwen-2.5")),
    "review": ("film_review", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                              "doubao-1.5-pro", "claude-3.5-haiku", "gemini-2.5-flash",
                              "gpt-3.5-turbo", "qwen-2.5")),
    "qa": ("question_answer", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                              "doubao-1.5-pro", "claude-3.5-haiku", "gemini-2.5-flash",
                              "gpt-3.5-turbo", "qwen-2.5")),
}

SAMPLE_PER_FILE = 120  # 每个文件抽头 n 篇——侦察要快，全量留给校准


def load_domain(corpus_dir: Path, domain: str, per_file: int):
    prefix, models = DOMAINS[domain]
    rows = []
    files = [(f"{prefix}_{m}.csv", True, m) for m in models]
    files.append((f"{prefix}_human.csv", False, "human"))
    for fname, is_ai, src in files:
        path = corpus_dir / "cred" / fname
        if not path.exists():
            print(f"  [缺] {fname}", file=sys.stderr)
            continue
        import csv
        n = 0
        with path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("text") or "").strip()
                if len(t) >= 120:
                    rows.append({"text": t, "ai": is_ai, "src": src})
                    n += 1
                if n >= per_file:
                    break
    return rows


def feat_auroc(rows, key):
    ai = [r[key] for r in rows if r["ai"] and r[key] == r[key]]
    hu = [r[key] for r in rows if not r["ai"] and r[key] == r[key]]
    return round(auroc(ai, hu), 3) if ai and hu else None


def extract(text: str, rules):
    r = engine.analyze(text, rules)
    st = r.doc_stats
    n = max(st.n_sentences, 1)
    ungated = sum(engine._SCORE_WEIGHT.get(f.severity, 1.0)
                  for f in r.findings + r.hints if f.para >= 0)
    per_rule = defaultdict(int)
    for f in r.findings + r.hints:
        per_rule[f.rule_id] += 1
    return {
        "hit_density": ungated / n,
        "sentence_cv": st.sentence_cv,
        "ttr": st.ttr,
        "ngram_repeat": st.ngram_repeat,
        "conn_density": st.conn_density,
        "n_sentences": st.n_sentences,
        "n_chars": st.n_chars,
        "_rules": per_rule,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "_qa" / "corpus"))
    ap.add_argument("--out", default=str(ROOT / "_qa" / "domain-recon.md"))
    ap.add_argument("--sample", type=int, default=SAMPLE_PER_FILE)
    args = ap.parse_args()

    profiles = engine.available_profiles()
    lines = ["# 多文体侦察报告", "",
             f"抽样：每文件头 {args.sample} 篇（≥120 字）。AUROC 以 AI 为正类。", ""]

    for domain in DOMAINS:
        rows = load_domain(Path(args.corpus), domain, args.sample)
        if not rows:
            lines += [f"## {domain}", "", "（语料缺失，跳过）", ""]
            continue
        n_ai = sum(1 for r in rows if r["ai"])
        lines += [f"## {domain}（{len(rows)} 篇：AI {n_ai} / 真人 {len(rows) - n_ai}）", ""]

        # 形态
        for prof in profiles:
            ex = [extract(r["text"], prof) for r in rows]
            ex_rows = list(zip(rows, ex))
            stats = [e for _, e in ex_rows]
            pass8 = sum(1 for e in stats if e["n_sentences"] >= 8)
            ai_pass = sum(1 for (r, e) in ex_rows if r["ai"] and e["n_sentences"] >= 8)
            hu_pass = sum(1 for (r, e) in ex_rows if not r["ai"] and e["n_sentences"] >= 8)
            chars = sorted(e["n_chars"] for e in stats)
            med = chars[len(chars) // 2] if chars else 0
            lines.append(f"- **{prof}**：过 8 句 {pass8}/{len(stats)}"
                         f"（AI {ai_pass} / 真人 {hu_pass}），字数中位 {med}")

        # 用过 8 句比例最高的 profile 算统计特征 AUROC；逐规则取全部句子级命中
        best_prof = max(profiles, key=lambda p: sum(
            1 for r in rows if engine.analyze(r["text"], p).doc_stats.n_sentences >= 8))
        ex_rows = [(r, extract(r["text"], best_prof)) for r in rows]
        lines += ["", f"单特征 AUROC（词表 = {best_prof}）：", ""]
        lines.append("| 特征 | AUROC |")
        lines.append("|---|---|")
        for feat in ("hit_density", "sentence_cv", "ttr", "ngram_repeat", "conn_density"):
            v = feat_auroc([{"ai": r["ai"], feat: e[feat]} for r, e in ex_rows], feat)
            lines.append(f"| {feat} | {v} |")

        # 逐规则：命中率的真人/AI 差（AUROC 在命中率上近似=秩和）
        rule_hits = defaultdict(lambda: {"ai": [0, 0], "hu": [0, 0]})
        for r, e in ex_rows:
            cls = "ai" if r["ai"] else "hu"
            for rid in {rid for rid in e["_rules"]}:
                rule_hits[rid][cls][1] += 1
            for rid, cnt in e["_rules"].items():
                rule_hits[rid][cls][0] += min(cnt, 3)  # 上限截断防长文爆计数
        lines += ["", "| 规则 | AI 命中率 | 真人命中率 | 方向 |", "|---|---|---|---|"]
        for rid, d in sorted(rule_hits.items()):
            ai_rate = d["ai"][1] and round(d["ai"][0] / max(d["ai"][1], 1), 3)
            hu_rate = d["hu"][1] and round(d["hu"][0] / max(d["hu"][1], 1), 3)
            if not (d["ai"][1] and d["hu"][1]):
                continue
            direction = "AI↑" if (ai_rate or 0) > (hu_rate or 0) else "真人↑"
            lines.append(f"| {rid} | {ai_rate} | {hu_rate} | {direction} |")
        lines.append("")

    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {args.out}")


if __name__ == "__main__":
    main()
