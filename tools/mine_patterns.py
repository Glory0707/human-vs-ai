"""词表挖掘：候选模式在某域语料上的 真人/AI 篇命中率差（grep 验证的自动化）。

纪律：词表必须对着语料验证——本工具把"grep 语料"变成可复现的评测。
候选模式 = 既有四库全部模式 + 文体候选库；命中差 |diff| 显著的模式才
有资格进新词表，方向反了的记进砍掉清单。

用法：python tools/mine_patterns.py --domain news --profile news --sample 300
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 文体候选库：写作时人工拟的模式假设，交由语料裁决
GENRE_BANK = {
    "news": [
        r"记者(获悉|了解到|从.{0,12}获悉)",
        r"据悉",
        r"据报道|据了解",
        r"有关(负责人|人士)表示",
        r"(持续|扎实|有序|稳步)推进",
        r"深入开展",
        r"全面(加强|提升|部署)",
        r"近日，?",
        r"日前，?",
        r"(迎来|进入).{0,8}(高峰|热潮|新阶段)",
        r"(下一步|下一步，)",
        r"值得注意的是",
        r"总体(来看|而言)",
    ],
    "essay": [
        r"人生(就像|如同)",
        r"正如.{2,12}(所说|所言|而言)",
        r"有一句话(说得好|这样说的)",
        r"纵观(古今|历史|千年)",
        r"综上所述",
        r"由此(可见|观之)",
        r"因此，?我们(要|应当|应该)",
        r"让我们(携手|共同)",
        r"(谱写|书写|绘就).{0,8}(篇章|华章|画卷)",
        r"(绽放|散发).{0,6}(光彩|光芒)",
        r"(扬帆|启航|远航)",
        r"不仅.{0,20}更是",
        r"不是.{0,16}而是",
        r"总之，?",
        r"所以说，?",
        r"作为.{2,10}(的我们|，我们)",
        r"(青春|奋斗|梦想)",
    ],
    "review": [
        r"值得一提",
        r"不得不说",
        r"总的来说|总而言之",
        r"瑕不掩瑜",
        r"可圈可点",
        r"(诚意|走心|用心)之作",
        r"(泪目|破防|封神|拉胯|好哭)",
        r"首先[，,].{0,40}其次",
        r"(导演|编剧|演员)的?(功力|诚意|水平)",
        r"(值得|不值得一)看",
        r"( reservations|安利|种草)",
        r"(高开低走|低开高走)",
        r"总体(来说|而言)",
    ],
}


def load_yaml_patterns(profile: str) -> dict[str, str]:
    """既有库的全部模式（供对照与复用判断）。"""
    import yaml
    path = ROOT / "human_vs_ai" / "rules" / f"{profile}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = {}
    for r in data.get("rules", []):
        for p in r.get("patterns", []):
            out.setdefault(p, r["id"])
    return out


# C-ReD 域名 → (文件前缀, 生成器, 文体候选库 key)
_DOMAINS = {
    "composition": ("composition", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                                    "doubao-1.5-pro", "claude-3.5-haiku"), "essay"),
    "news": ("news", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                      "doubao-1.5-pro", "claude-3.5-haiku"), "news"),
    "review": ("film_review", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                              "doubao-1.5-pro", "claude-3.5-haiku"), "review"),
    "qa": ("question_answer", ("deepseek-r1", "deepseek-v3", "gpt-4o", "qwen-3",
                              "doubao-1.5-pro", "claude-3.5-haiku"), "qa"),
}


def load_balanced(corpus_dir: Path, prefix: str, models, per_side: int, seed: int):
    rng = random.Random(seed)
    texts = {"ai": [], "hu": []}
    for m in models:
        path = corpus_dir / "cred" / f"{prefix}_{m}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as f:
            pool = [r.get("text") or "" for r in csv.DictReader(f)]
        pool = [t.strip() for t in pool if len(t.strip()) >= 120]
        rng.shuffle(pool)
        texts["ai"] += pool[: per_side // max(len(models), 1) + 1]
    hu_path = corpus_dir / "cred" / f"{prefix}_human.csv"
    with hu_path.open(encoding="utf-8-sig") as f:
        pool = [r.get("text") or "" for r in csv.DictReader(f)]
    pool = [t.strip() for t in pool if len(t.strip()) >= 120]
    rng.shuffle(pool)
    texts["hu"] = pool[:per_side]
    n = min(len(texts["ai"]), len(texts["hu"]))
    texts["ai"] = texts["ai"][:n]
    texts["hu"] = texts["hu"][:n]
    return texts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="composition/news/review/qa")
    ap.add_argument("--profile", help="同时对照该既有库的模式")
    ap.add_argument("--sample", type=int, default=250, help="每侧篇数")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    prefix, models, _ = _DOMAINS[args.domain]

    corpus = ROOT / "_qa" / "corpus"
    texts = load_balanced(corpus, prefix, models, args.sample, args.seed)
    print(f"域 {args.domain}：AI {len(texts['ai'])} 篇 / 真人 {len(texts['hu'])} 篇\n")

    candidates: dict[str, str] = {}
    for pat in GENRE_BANK.get(_DOMAINS[args.domain][2], []):
        candidates[pat] = "genre"
    if args.profile:
        for pat, rid in load_yaml_patterns(args.profile).items():
            candidates.setdefault(pat, rid)

    rows = []
    for pat, origin in candidates.items():
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        ai = sum(1 for t in texts["ai"] if rx.search(t))
        hu = sum(1 for t in texts["hu"] if rx.search(t))
        rows.append((abs(ai - hu) / args.sample, ai, hu, pat, origin))
    rows.sort(reverse=True)
    print(f"{'diff':>6}  {'AI':>4}  {'真人':>4}  {'来源':<14} 模式")
    for diff, ai, hu, pat, origin in rows:
        if diff < 0.01:
            continue
        print(f"{diff:6.3f}  {ai:4d}  {hu:4d}  {origin:<14} {pat}")


if __name__ == "__main__":
    main()
