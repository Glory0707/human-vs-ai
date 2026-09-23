"""对抗自评测：拿自己的改写器当攻击者，量化"规避容易度"。

动机：规则是开源的，模型/用户一旦照着改写建议改，检测还能剩多少
区分度？本脚本模拟"服从建议的作者"——删类建议删行、改类建议换
候选文本——统计改写前后 AI 味指数与召回的变化，并分层对比：

- 词表层命中（可规避）：改写建议直接针对词表，预期大幅下降
- 统计层（难规避）：句长节奏/TTR 是全文属性，逐行替换难以伪装

结论用于规则演化方向：若统计层也被轻易规避，说明评分底盘需要补
特征；若词表层降完仍有区分度，说明底盘扎实。

运行：python tools/adversarial_eval.py [--n 200] [--domain composition]
语料不入库（_qa/corpus/ 已 gitignore），报告入库 _qa/adversarial-eval.md。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import engine, rewrite, segment  # noqa: E402
from human_vs_ai.rewrite import DELETE, REWRITE  # noqa: E402

TH = 60  # 与现场验证同一判定阈值

# LLM 攻击者：按本项目的规则常识"洗稿"——对抗评测要模拟的实战场景。
# prompt 把检测依赖的三件事告诉攻击方（节奏/词表/排比），看还剩多少区分度。
LLM_PROMPT = (
    "请改写下面的中文文章，让它读起来更像人写的：把均匀的长句打散成"
    "长短交错的句子、删掉模板连接词和排比堆砌、换掉万能开场白与"
    "总结腔收尾。保留全部信息与观点，不要新增内容。直接输出改写后"
    "的全文，不要任何解释。\n\n"
)


def llm_rewrite(text: str, api_file: Path) -> str:
    """调火山方舟当攻击者。key 从外部文件读，不写入仓库。
    429 退避重试——与其他 API 任务并行时限流常见。"""
    import json
    import time
    import urllib.error
    import urllib.request
    keys = [l.split("：", 1)[1].strip()
            for l in api_file.read_text(encoding="utf-8").splitlines()
            if l.startswith("api key")]
    req_data = json.dumps({"model": "doubao-seed-2.1-pro",
                           "messages": [{"role": "user",
                                         "content": LLM_PROMPT + text}]}).encode()
    headers = {"Authorization": f"Bearer {keys[0]}",
               "Content-Type": "application/json"}
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                data=req_data, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(30 * (attempt + 1))
                continue
            raise


def attack(text: str, profile: str) -> str:
    """服从建议的作者：删类删行、改类换候选、其余保留。

    改写器按行判定（与 JS 同构的行级契约），而 AI 生成文本多为整段
    散文——整段喂进去只会得到方向提示、无候选可换。先逐句成行再
    攻击，模拟"逐句照建议改"的真实规避路径。
    """
    lines = [s.text for s in segment.split_sentences(text) if s.text.strip()]
    if len(lines) == 1:
        lines = [l for l in text.splitlines() if l.strip()]  # 单句长行退回行级
    result = rewrite.rewrite_text("\n".join(lines), profile)
    out = []
    for raw, adv in zip(lines, result.advices):
        if adv.action == DELETE:
            continue
        if adv.action == REWRITE and adv.candidate:
            out.append(adv.candidate)
        else:
            out.append(raw)
    return "".join(out)


def lexical_hits(result) -> int:
    return sum(1 for f in result.findings + result.hints
               if f.tier == "lexical")


def statistical_hits(result) -> int:
    return sum(1 for f in result.findings
               if f.tier == "statistical")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="composition",
                    choices=["composition", "news"])
    ap.add_argument("--profile", default=None,
                    help="默认 composition→essay / news→news")
    ap.add_argument("--n", type=int, default=200, help="每模型抽样篇数")
    ap.add_argument("--attacker", default="rewriter",
                    choices=["rewriter", "llm"],
                    help="rewriter=本项目改写器；llm=火山模型按规则常识洗稿")
    ap.add_argument("--api-file", type=Path, default=Path(r"D:/科研/API.txt"))
    ap.add_argument("--report", type=Path,
                    default=ROOT / "_qa" / "adversarial-eval.md")
    args = ap.parse_args()
    profile = args.profile or ("essay" if args.domain == "composition" else "news")

    corpus = ROOT / "_qa" / "corpus" / "cred"
    path = corpus / f"{args.domain}_human.csv"
    if not path.exists():
        sys.exit(f"语料缺失：{path}")

    pairs = []  # (s0, s1, lex0, lex1, stat0, stat1)
    models = ["claude-3.5-haiku", "deepseek-v3", "gpt-4o", "qwen-2.5", "qwen-3"]
    for m in models:
        src = corpus / f"{args.domain}_{m}.csv"
        if not src.exists():
            continue
        n = 0
        with src.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("text") or "").strip()
                if len(t) < 200:
                    continue
                r0 = engine.analyze(t, profile)
                if r0.score is None or r0.score.index < TH:
                    continue  # 只攻击"被抓到的"——对抗才有意义
                t1 = (llm_rewrite(t, args.api_file) if args.attacker == "llm"
                      else attack(t, profile))
                r1 = engine.analyze(t1, profile)
                if r1.score is None:
                    continue
                pairs.append((r0.score.index, r1.score.index,
                              lexical_hits(r0), lexical_hits(r1),
                              statistical_hits(r0), statistical_hits(r1)))
                n += 1
                if n >= args.n:
                    break

    if not pairs:
        sys.exit("没有可攻击样本（≥60 分的长文为 0）")

    n = len(pairs)
    avg = lambda xs: sum(xs) / n  # noqa: E731
    still = sum(1 for s0, s1, *_ in pairs if s1 >= TH)
    drop = avg([s0 - s1 for s0, s1, *_ in pairs])
    lex0, lex1 = avg([p[2] for p in pairs]), avg([p[3] for p in pairs])
    st0, st1 = avg([p[4] for p in pairs]), avg([p[5] for p in pairs])

    lines = [
        "# 对抗自评测：改写器当攻击者",
        "",
        f"语料：C-ReD {args.domain}（{', '.join(models)}），每模型 ≤{args.n} 篇，"
        f"只攻击原文 ≥{TH} 分的样本，共 {n} 篇。",
        "",
        ("攻击方式：doubao-seed-2.1-pro 按检测依赖的规则常识洗稿"
         "（打散节奏/删模板词/去排比）——实战级对抗。"
         if args.attacker == "llm" else
         "攻击方式：服从本项目改写器的建议（删类删行、改类换候选文本，逐句成行）。"),
        "",
        "| 指标 | 改写前 | 改写后 | 变化 |",
        "|---|---|---|---|",
        f"| 平均指数 | {avg([p[0] for p in pairs]):.1f} | {avg([p[1] for p in pairs]):.1f} | −{drop:.1f} |",
        f"| ≥{TH} 分占比（攻击后仍被抓） | 100% | {still/n:.1%} | −{1-still/n:.1%} |",
        f"| 词表层命中（均） | {lex0:.2f} | {lex1:.2f} | −{lex0-lex1:.2f}（{(lex0-lex1)/lex0:.0%}） |",
        f"| 统计层命中（均） | {st0:.2f} | {st1:.2f} | −{st0-st1:.2f}（{(st0-st1)/max(st0,0.01):.0%}） |",
        "",
    ]
    still_rate = still / n
    if still_rate > 0.5:
        concl = ("过半样本删词表后仍超阈值——区分度主要来自统计底盘"
                 "（句长节奏/词汇），词表层被规避后底盘仍兜底，评分方向扎实。")
    elif still_rate > 0.2:
        concl = ("词表层规避后部分样本跌破阈值——底盘有兜底但边际变薄，"
                 "统计特征值得继续补强（详见 design.md §4 排队）。")
    else:
        concl = ("改写建议几乎完全规避了检测——评分过度依赖词表层，"
                 "必须补统计/结构特征（升级为高优先级）。")
    lines.append(f"## 结论\n\n{concl}")
    lines.append("")

    args.report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
