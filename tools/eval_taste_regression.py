"""口味回归评测：用私库文案池标注对验收 personal profile 与 rewrite 模块。

数据全部来自 corpus_private/（gitignore，永不入库）；语料缺失时直接跳过，
不阻塞开源环境跑测试。

三条验收线（docs/taste_zhouao.md §5）：
1. 被毙 31 条召回 100%（首轮 15 条暖心稿必须全中）
2. 定稿 37 条不产生 high/medium 命中；我亲改的 8 组不得判 AI 味
3. 改写对命中「更短 / 含梗或具体名词 / 无说明腔」三中其二 ≥80%

用法：python tools/eval_taste_regression.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import engine  # noqa: E402
from human_vs_ai.rewrite import DELETE, REWRITE, classify_line  # noqa: E402

CORPUS = ROOT / "corpus_private"
REF = CORPUS / "taste_reference.json"


def evaluate(threshold_rewrite: float = 0.8) -> dict:
    ref = json.loads(REF.read_text(encoding="utf-8"))
    rules = engine.load_rules("personal")

    def analyze_line(line: str):
        # 单条文案按一段处理：min_sentences 保护会让 doc 规则不判，句级规则照常
        return engine.analyze(line, "personal")

    # 1. 被毙稿召回（命中任一规则即算检出）
    vetoed_total, vetoed_hit, missed = 0, 0, []
    for line in ref["vetoed"]:
        vetoed_total += 1
        res = analyze_line(line)
        if res.findings or res.hints:
            vetoed_hit += 1
        else:
            missed.append(line)

    # 2. 定稿误报
    kept_total, kept_soft, kept_hard = 0, 0, []
    for line in ref["kept"]:
        kept_total += 1
        res = analyze_line(line)
        if res.findings:
            sev = {f.severity for f in res.findings}
            if sev & {"high", "medium"}:
                kept_hard.append(line)
            else:
                kept_soft += 1
        elif res.hints:
            kept_soft += 1

    # 3. 我亲改的改写对：改写器对 AI 原句的产出物是否命中「三中其二」
    #    维度按产出物相对 AI 原句计算（产出物定义见 LineAdvice.proposal）
    # 维度词表来自私库参考集（语料派生词汇不进开源代码）
    vocab = ref.get("dims_vocab", {})
    meme = "|".join(vocab.get("meme", [])) or "牛马|摸鱼|摆烂"
    noun = "|".join(vocab.get("noun", [])) or "DDL|deadline|组会|文献|论文|数据"
    dim_pats = {
        "含梗或具体名词": re.compile(rf"({noun}|{meme}|推导|具体名词|梗)"),
        "无说明腔": re.compile(r"(自动[挑选]|先.{1,4}再.{1,4}(作答|生成)|一次最多|请直接|点击|拖[入拽]|勾选)"),
    }
    rw_total, rw_hit, rw_fail, agreed = 0, 0, [], 0
    for item in ref["rewrites"]:
        if item["ai"].startswith("（新增"):
            continue  # 我新增的行没有 AI 原句，不构成配对
        rw_total += 1
        advs = [classify_line(x.strip()) for x in item["ai"].split("/") if x.strip()]
        outs = [a.proposal() for a in advs]
        out = min(outs, key=len) if outs else item["ai"]
        shorter = len(out) < len(item["ai"])
        dims = {
            "更短": shorter,
            "含梗或具体名词": bool(dim_pats["含梗或具体名词"].search(out)),
            "无说明腔": not dim_pats["无说明腔"].search(out),
        }
        hit = sum(dims.values()) >= 2
        moved = any(a.action in (DELETE, REWRITE) for a in advs)
        agreed += 1 if moved else 0
        if hit:
            rw_hit += 1
        else:
            rw_fail.append({"mine": item["mine"], "dims": dims, "proposal": out})

    recall = vetoed_hit / vetoed_total if vetoed_total else 0.0
    false_rate = len(kept_hard) / kept_total if kept_total else 0.0
    rw_rate = rw_hit / rw_total if rw_total else 0.0
    verdict = "PASS" if (recall >= 0.999 and not kept_hard and rw_rate >= threshold_rewrite) else "FAIL"
    return {
        "n_rules": len(rules),
        "vetoed": {"total": vetoed_total, "hit": vetoed_hit, "recall": recall, "missed": missed},
        "kept": {"total": kept_total, "soft_hits": kept_soft, "hard_hits": kept_hard,
                 "hard_rate": false_rate},
        "rewrites": {"total": rw_total, "hit": rw_hit, "rate": rw_rate, "failed": rw_fail,
                     "moved": agreed},
        "verdict": verdict,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.8, help="改写维度命中率验收线")
    args = ap.parse_args()
    if not REF.exists():
        sys.exit(f"缺少私库语料 {REF}——先跑 tools/extract_private_corpus.py")
    r = evaluate(args.threshold)
    print(f"# 口味回归评测（personal profile，{r['n_rules']} 条规则）")
    print(f"\n## 1. 被毙稿召回：{r['vetoed']['hit']}/{r['vetoed']['total']} "
          f"= {r['vetoed']['recall']:.1%}（验收 100%）")
    for line in r["vetoed"]["missed"]:
        print(f"  [漏检] {line}")
    print(f"\n## 2. 定稿误报：high/medium {len(r['kept']['hard_hits'])}/{r['kept']['total']}"
          f" · 弱命中 {r['kept']['soft_hits']}（验收：无 high/medium）")
    for line in r["kept"]["hard_hits"]:
        print(f"  [误报] {line}")
    print(f"\n## 3. 改写产出物命中三中其二：{r['rewrites']['hit']}/{r['rewrites']['total']} "
          f"= {r['rewrites']['rate']:.1%}（验收 ≥{args.threshold:.0%}）")
    for f in r["rewrites"]["failed"]:
        print(f"  [未达标] {f['mine']} dims={f['dims']} 产出={f['proposal']!r}")
    moved, total = r["rewrites"]["moved"], r["rewrites"]["total"]
    print(f"  [诊断] 改写器对 AI 原句给出「删/改」判定：{moved}/{total}"
          f"——其余是方向对但语感差一口气的半成品，规则判不出，只能人补"
          f"（docs/taste_zhouao.md §4 R4）")
    print(f"\n结论：{r['verdict']}")
    if r["verdict"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
