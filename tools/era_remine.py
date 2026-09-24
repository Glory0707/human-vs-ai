"""词表 era 重挖：C-ReD 各模型子集上挖掘 AI 过采样短语 + 体检现役词表。

季度性重跑（design.md §4 P3），观察指纹漂移：
  - 新候选：AI 池化 doc 覆盖显著高于真人的 3-8 字短语（粗筛，入词表前须
    人工 grep 验证语境——T4.1 方法论：词表必须对着语料验证表达频率）
  - 现役体检：对应 profile 词表逐 pattern 算 AI/human doc 覆盖比，
    <2 报退化、<1 报反转（只报告不改规则，改动走正式校准轮）

口径与引擎一致：pattern 用 re.compile(p).search() 判 doc 命中。
产出：_qa/era-remine-<domain>.md

用法：python tools/era_remine.py [--domain paper] [--top 40]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
CORPUS = ROOT / "_qa" / "corpus" / "cred"
OUT = ROOT / "_qa"

DOMAIN_PROFILE = {"paper": "academic", "composition": "essay", "news": "news",
                  "film_review": "review", "question_answer": "general"}
MIN_LEN = 120  # 与 evaluate_cred 同门槛：太短的代表不了文体
RATIO_KEEP = 5.0  # 候选门槛：AI/human doc 覆盖比
COVER_KEEP = 0.05  # 候选门槛：AI 池化 doc 覆盖 ≥5%
NGRAM = range(3, 9)


def load_docs(domain: str) -> tuple[dict[str, list[str]], list[str]]:
    """→ ({source: 文本列表}, [human, *模型])；缺语料给出下载指引。"""
    paths = sorted(CORPUS.glob(f"{domain}_*.csv"))
    if not paths:
        sys.exit(f"缺少语料 {CORPUS}/{domain}_*.csv（下载见 docs/rules.md 校准章节）")
    docs: dict[str, list[str]] = {}
    for p in paths:
        src = p.stem[len(domain) + 1:]
        texts = []
        with p.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("text") or "").strip()
                if len(t) >= MIN_LEN:
                    texts.append(t)
        docs[src] = texts
    order = ["human"] + sorted(s for s in docs if s != "human")
    return docs, order


def doc_grams(text: str) -> set[str]:
    """一篇文本 → 出现过的 3-8 字 n-gram（只留全字母数字串，天然跳过标点）。"""
    found: set[str] = set()
    for n in NGRAM:
        for i in range(len(text) - n + 1):
            g = text[i:i + n]
            if all(c.isalnum() for c in g):
                found.add(g)
    return found


def df_stats(gram_sets: list[set[str]]) -> Counter:
    """doc 频率：输入每篇文档的 gram 集（doc_grams 预计算，两侧行情共用一遍）。"""
    df: Counter = Counter()
    for gs in gram_sets:
        df.update(gs)
    return df


def collapse(cands: list[dict]) -> list[dict]:
    """重叠折叠：包含关系且覆盖相近（≤2x）视为同一信号，保留最长者；
    覆盖差 >2x 说明两串各有独立出现场景，都保留。"""
    cands = sorted(cands, key=lambda d: (-len(d["g"]), -d["frac_ai"]))
    kept: list[dict] = []
    for d in cands:
        same = any((k["g"] in d["g"] or d["g"] in k["g"])
                   and max(k["frac_ai"], d["frac_ai"])
                   / min(k["frac_ai"], d["frac_ai"]) <= 2.0 for k in kept)
        if not same:
            kept.append(d)
    return kept


def mine_candidates(docs: dict, order: list[str], top: int) -> list[dict]:
    hu, ai = docs["human"], [t for s in order[1:] for t in docs[s]]
    hu_df, ai_df = df_stats([doc_grams(t) for t in hu]), df_stats(
        [doc_grams(t) for t in ai])
    n_hu, n_ai = len(hu), len(ai)
    hu_floor = 0.5 / max(n_hu, 1)  # 真人 0 覆盖时的平滑分母
    cands = []
    for g, c in ai_df.items():
        if len(g) < 3 or c < COVER_KEEP * n_ai:
            continue
        frac_ai, frac_hu = c / n_ai, hu_df.get(g, 0) / n_hu
        ratio = frac_ai / max(frac_hu, hu_floor)
        if ratio >= RATIO_KEEP:
            cands.append({"g": g, "frac_ai": frac_ai, "frac_hu": frac_hu,
                          "ratio": ratio})
    kept = collapse(cands)
    # 每候选的模型级覆盖
    for d in kept[:top]:
        rx = re.compile(re.escape(d["g"]))
        d["per_model"] = {s: sum(1 for t in docs[s] if rx.search(t)) / len(docs[s])
                          for s in order[1:]}
    return kept[:top]


def audit_lexical(docs: dict, order: list[str], profile: str) -> list[dict]:
    """现役词表体检：lexical 层逐 pattern 算 doc 覆盖与覆盖比。"""
    rules_path = ROOT / "human_vs_ai" / "rules" / f"{profile}.yaml"
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    hu = docs["human"]
    ai = [t for s in order[1:] for t in docs[s]]
    n_hu, n_ai = len(hu), len(ai)
    rows = []
    for r in data["rules"]:
        if r.get("tier") != "lexical" or r.get("scope") != "sentence":
            continue
        for pat in r.get("patterns", []):
            rx = re.compile(pat)
            hu_c = sum(1 for t in hu if rx.search(t))
            ai_c = sum(1 for t in ai if rx.search(t))
            ratio = (ai_c / n_ai) / max(hu_c / n_hu, 0.5 / n_hu)
            rows.append({"rule": r["id"], "pat": pat, "ai": ai_c / n_ai,
                         "hu": hu_c / n_hu, "ratio": ratio, "support": ai_c + hu_c})
    rows.sort(key=lambda d: d["ratio"])
    return rows


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def status(ratio: float, support: int) -> str:
    if support < 5:  # 双侧合计命中 <5 篇：语料说不了话，不判
        return "样本不足（不判）"
    if ratio >= 5:
        return "正常"
    if ratio >= 2:
        return "边缘（观察）"
    if ratio >= 1:
        return "退化"
    return "反转"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="paper", choices=sorted(DOMAIN_PROFILE))
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    docs, order = load_docs(args.domain)
    sizes = " · ".join(f"{s} {len(docs[s])}" for s in order)
    print(f"[{args.domain}] {sizes}")
    cands = mine_candidates(docs, order, args.top)
    audit = audit_lexical(docs, order, DOMAIN_PROFILE[args.domain])

    models = order[1:]
    L = [f"# 词表 era 重挖：{args.domain} 域（{date.today().isoformat()}）", "",
         f"语料（C-ReD，≥{MIN_LEN} 字）：{sizes}。",
         "候选 = AI 池化 doc 覆盖 ≥5% 且 AI/真人覆盖比 ≥5 的 3-8 字极大短语；"
         "体检 = 现役 lexical pattern 的 doc 覆盖比（口径同引擎 re.search）。",
         "候选只是粗筛，入词表前须人工 grep 验证语境；体检退化只报告，"
         "改动走正式校准轮。各 profile 词表的标定语料不必是本域，"
         "退化标记≠删词依据（跨语料覆盖差异会误报退化）。", "",
         "## 新指纹候选", "",
         "| 短语 | AI doc | 真人 doc | 倍数 | " + " | ".join(models) + " |",
         "|---|---|---|---|" + "---|" * len(models)]
    for d in cands:
        L.append(f"| {d['g']} | {pct(d['frac_ai'])} | {pct(d['frac_hu'])} | "
                 f"{d['ratio']:.0f}x | "
                 + " | ".join(pct(d['per_model'][m]) for m in models) + " |")
    L += ["", f"## 现役词表体检（{DOMAIN_PROFILE[args.domain]} profile）", "",
          "| 规则 | pattern | AI doc | 真人 doc | 倍数 | 状态 |", "|---|---|---|---|---|---|"]
    for r in audit:
        L.append(f"| {r['rule']} | {r['pat']} | {pct(r['ai'])} | {pct(r['hu'])} | "
                 f"{r['ratio']:.1f}x | {status(r['ratio'], r['support'])} |")
    degraded = [r for r in audit if r["ratio"] < 2 and r["support"] >= 5]
    L += ["", f"结论：候选 {len(cands)} 条、现役 {len(audit)} 条，"
             f"其中退化/反转 {len(degraded)} 条"
             + ("——" + "、".join(f"{r['rule']}（{r['pat']}）" for r in degraded)
                if degraded else "。")]

    out = OUT / f"era-remine-{args.domain}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已写 {out}：候选 {len(cands)}，体检 {len(audit)}（退化 {len(degraded)}）")


if __name__ == "__main__":
    main()
