"""公文场景评测（T4.2）：真人政府公文误报率（验收 <20%）+ AI 样本召回。

语料：
- 真人：_qa/corpus/gov/*.txt（中国政府网公开公文，tools/fetch_gov_corpus.py 抓取）
- AI：tests/data/ai_official.txt（构造，4 篇典型 AI 味公文，按【AI-n …】分段）

误报判定：真人公文里的每一处命中逐条人工核对（--show 打印上下文），
统计含 ≥1 处 non-hint 命中的公文占比 = 误报率。

用法：python tools/evaluate_official.py --show
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from human_vs_ai import engine  # noqa: E402

ROOT = Path(__file__).parent.parent
GOV = ROOT / "_qa" / "corpus" / "gov"
AI_FIXTURE = ROOT / "tests" / "data" / "ai_official.txt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="打印每处命中供人工核对")
    args = ap.parse_args()

    files = sorted(GOV.glob("*.txt"))
    if not files:
        sys.exit(f"缺少真人公文语料 {GOV}——先跑 tools/fetch_gov_corpus.py")

    lines = ["# 公文场景评测（official profile）", ""]
    flagged = 0
    total_hits = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        result = engine.analyze(text, "official")
        findings = result.findings
        total_hits += len(findings)
        if findings:
            flagged += 1
        lines.append(f"## {f.name}（{result.doc_stats.n_sentences} 句 / {result.doc_stats.n_chars} 字）")
        lines.append("")
        if not findings:
            lines.append("无命中。")
            lines.append("")
            continue
        for fd in findings:
            if fd.para >= 0:
                show = fd.sentence if len(fd.sentence) <= 60 else fd.sentence[:57] + "…"
                lines.append(f"- [{fd.severity}] {fd.rule_id}：「{show}」 命中：{'、'.join(fd.matches[:3])}")
            else:
                lines.append(f"- [{fd.severity}] {fd.rule_id}（全文）：{fd.matches[0]}")
        if result.hints:
            lines.append(f"- （{len(result.hints)} 处孤立弱命中未计入）")
        lines.append("")

    rate = flagged / len(files)

    ai_text = AI_FIXTURE.read_text(encoding="utf-8")
    ai_result = engine.analyze(ai_text, "official")
    ai_hits = len(ai_result.findings)

    verdict = "PASS" if rate < 0.2 else "FAIL"
    lines.insert(2, f"真人公文误报率（≥1 处 non-hint 命中的文件占比）：{flagged}/{len(files)} = {rate:.1%}（验收 <20% → {verdict}）")
    lines.insert(3, f"AI 样本召回：{ai_hits} 处命中")
    lines.insert(4, "")

    out = Path("_qa/eval-official.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    if args.show:
        print("\n".join(lines))
    else:
        print(f"误报率 {flagged}/{len(files)} = {rate:.1%}（{verdict}）· AI 样本命中 {ai_hits} 处")
    print(f"详单：{out}")


if __name__ == "__main__":
    main()
