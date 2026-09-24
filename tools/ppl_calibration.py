"""句级困惑度标定：AI（gen-oos 四模型）vs 真人（豆瓣/果壳），doc 级特征 AUROC。

语料与 oos_check 同一批样本外问答集（gitignore）。每人/每模型文档抽
--sample 篇（seed 固定可复现），特征取句级 PPL 的聚合：中位 / 均值 /
最顺滑句（p10）。PPL 方向 = AI 更低，AUROC < 0.5 时区分度取 1-AUROC。

运行前提：torch/transformers + 模型权重（默认 `_qa/models/qwen3-0.6b`，
下载：`HF_ENDPOINT=https://hf-mirror.com` snapshot_download）。
产出 `_qa/ppl-calibration.md`。

用法:python tools/ppl_calibration.py [--sample 25] [--model 路径]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fit_score_tiers import auroc  # noqa: E402
from human_vs_ai import engine  # noqa: E402

GEN = ROOT / "_qa/corpus/gen-oos/qa.jsonl"
QA_HU = ROOT / "_qa/corpus/oos-qa"
OUT = ROOT / "_qa/ppl-calibration.md"

_SPAM = re.compile(r"首发|公众号|公号：|扫码|微信号|搬运|转载|出处[:：]|Appcaret敬请")


def load_docs(sample: int) -> tuple[list[dict], list[dict]]:
    ai = [json.loads(l)["text"] for l in GEN.read_text(encoding="utf-8").splitlines()
          if l.strip()][:sample]
    hu: list[str] = []
    for f in sorted(QA_HU.glob("*.json")):
        for it in json.loads(f.read_text(encoding="utf-8"))["items"]:
            if _SPAM.search(it["text"][:120]) or _SPAM.search(it["text"][-120:]):
                continue
            hu.append(it["text"])
    hu.sort(key=lambda t: __import__("hashlib").sha256(t.encode()).hexdigest())
    hu = hu[:sample]
    return ai, hu


def doc_features(text: str, scorer) -> dict | None:
    """一篇文档 → 句级 PPL 聚合特征（不够 8 句丢弃，与评分门槛同口径）。"""
    sents = [s.text for blk in engine.segment.split_document(text) for s in blk.sents]
    if len(sents) < 8:
        return None
    stats = [st for st in scorer.score_sentences(sents) if st]
    if len(stats) < 8:
        return None
    ppls = sorted(st["ppl"] for st in stats)
    n = len(ppls)
    return {"median_ppl": ppls[n // 2],
            "mean_ppl": statistics.mean(ppls),
            "p10_ppl": ppls[max(0, int(n * 0.1))]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--model", default=str(ROOT / "_qa/models/qwen3-0.6b"))
    args = ap.parse_args()

    from human_vs_ai import ppl
    scorer = ppl.SentenceScorer(args.model)

    ai, hu = load_docs(args.sample)
    ai_f = [f for t in ai if (f := doc_features(t, scorer))]
    hu_f = [f for t in hu if (f := doc_features(t, scorer))]
    print(f"AI {len(ai_f)}/{len(ai)} · 真人 {len(hu_f)}/{len(hu)} 出特征")

    L = ["# 句级困惑度标定（P3 句级困惑度，冻结零训练）", "",
         f"模型：{args.model}（HF 格式；PPL 口径见 human_vs_ai/ppl.py）。",
         f"语料：AI gen-oos 四模型 vs 真人豆瓣/果壳（样本外问答集，`--sample {args.sample}`）。",
         "方向 = AI 更顺滑（PPL 更低），AUROC < 0.5 时区分度取 1-AUROC。", "",
         "| doc 级特征 | AUROC | 区分度（AI 顺滑方向） |", "|---|---|---|"]
    best = 0.0
    for feat in ("median_ppl", "mean_ppl", "p10_ppl"):
        a = [f[feat] for f in ai_f]
        h = [f[feat] for f in hu_f]
        auc = auroc(a, h)
        sep = max(auc, 1 - auc)
        best = max(best, sep)
        L.append(f"| {feat} | {auc:.3f} | **{sep:.3f}** |")
    L += ["", f"结论：{'有方向性信号' if best >= 0.7 else '信号弱'}"
             f"（最强 doc 特征区分度 {best:.3f}），作为统计层之外的互补视角入可选依赖。", ""]

    med = lambda fs: statistics.median([f["median_ppl"] for f in fs])
    L += [f"AI median_ppl 中位 {med(ai_f):.1f} · 真人 {med(hu_f):.1f}", ""]
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
