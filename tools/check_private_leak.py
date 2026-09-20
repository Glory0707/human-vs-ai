"""开源文件私人语料泄漏检查：任何与私库语料重合的 6 字级中文片段都不许
出现在入库文件里（persona-stream 的 data/ 与 corpus_private/ 都不入库，
开源侧只能出现合成样例——这是硬边界，靠本脚本守住）。

做法：把标注链的每条文案切成 6 字滑窗，逐个在目标文件里搜。6 字连续相同
基本排除巧合；纯 ASCII 片段（accept 这类通用术语）与产品名不算语料原文。

用法：python tools/check_private_leak.py [文件...]
     默认检查全部入库的规则库与文档；语料缺失时跳过（返回 0），不阻塞开源环境。
"""
import json, re, sys
from pathlib import Path
REF = Path("corpus_private/taste_reference.json")
if not REF.exists():
    print("无语料，跳过"); sys.exit(0)
ref = json.loads(REF.read_text(encoding="utf-8"))
pool = ref["vetoed"] + ref["kept"] + [r["ai"] for r in ref["rewrites"]] + [r["mine"] for r in ref["rewrites"]]
def frags(s):
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)
    return {s[i:i+6] for i in range(len(s)-5)} if len(s) >= 6 else set()
BAD = set()
for s in pool:
    BAD |= frags(s)
# 纯 ASCII 片段（通用英文术语）与产品名不算语料原文
BAD = {b for b in BAD if re.search(r"[\u4e00-\u9fff]", b) and "eggpaper".find(b) < 0}
DEFAULT_TARGETS = [
    "human_vs_ai/rules/personal.yaml",
    "docs/taste_zhouao.md",
    "README.md",
    "docs/rules.md",
    "docs/design.md",
]
targets = sys.argv[1:] or DEFAULT_TARGETS
bad = False
for f in targets:
    flat = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", Path(f).read_text(encoding="utf-8"))
    hits = sorted(b for b in BAD if b in flat)
    print(f"{f}: {'✓ 干净' if not hits else '✗ 泄漏 ' + str(hits)}")
    bad = bad or bool(hits)
sys.exit(1 if bad else 0)
