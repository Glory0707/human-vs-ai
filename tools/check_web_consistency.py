"""Python 引擎与浏览器引擎(JS)的一致性守护。

双实现最大的风险是静默漂移——切分差一个字符、统计差一次舍入,
两端报告就会各说各话。本脚本用固定语料集对两端跑 analyze,
findings/hints 逐条 diff、stats 数值按 4 位小数 diff(ttr/tokenizer
除外:浏览器无 jieba,口径不同;两端规则库均无 TTR 判定,不影响报告)。

运行:python tools/check_web_consistency.py   (需要 node 在 PATH)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import engine  # noqa: E402
from human_vs_ai.stats import _HAS_JIEBA  # noqa: E402

PROBE_TEXTS = [
    ("ai_fixture", (ROOT / "tests/data/ai_academic.txt").read_text(encoding="utf-8")),
    ("human_fixture", (ROOT / "tests/data/human_academic.txt").read_text(encoding="utf-8")),
    ("empty", ""),
    ("codeblock_only", "```python\nprint(1)\n```"),
    ("no_punct", "字" * 600),
    ("mixed_md", "# 标题\n\n| a | b |\n|---|---|\n\n随着人工智能的快速发展，方法很多。首先，试了 A。其次，试了 B。\n\n综上所述，一切很好。\n"),
    ("quotes", '他说"这一点很重要。然后走了。"后来回来了。这是独句段测试。'),
]

NODE_SCRIPT = r"""
const fs = require("fs");
const HvA = require(process.argv[2]);
const rules = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const texts = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const out = {};
for (const [name, text] of Object.entries(texts)) {
  out[name] = HvA.analyze(text, rules);
}
process.stdout.write(JSON.stringify(out));
"""


def rules_to_json(profile: str) -> list[dict]:
    rules = engine.load_rules(profile)
    return [
        {
            "id": r.id,
            "name": r.name,
            "tier": r.tier,
            "scope": r.scope,
            "severity": r.severity,
            "patterns": [p.pattern for p in r.patterns],
            "explanation": r.explanation,
            "suggestion": r.suggestion,
            "doc_metric": r.doc_metric,
            "doc_compare": r.doc_compare,
            # JSON 不认 NaN;无阈值规则传 null
            "doc_threshold": None if r.doc_threshold != r.doc_threshold else r.doc_threshold,
        }
        for r in rules
    ]


def normalize(result: dict) -> dict:
    """归一到可比形态:findings/hints 逐条全字段,stats 舍入 4 位(忽略 ttr/tokenizer)。"""
    def fs(fs_list):
        return [
            {k: f[k] for k in ("rule_id", "severity", "para", "sentence", "matches")}
            for f in fs_list
        ]

    s = result["stats"]

    def norm_num(v):
        # NaN → None(JS 端 JSON 序列化已是 null),数值 round 到 4 位
        if isinstance(v, float) and v != v:
            return None
        if isinstance(v, (int, float)):
            return round(float(v), 4)
        return v

    return {
        "findings": fs(result["findings"]),
        "hints": fs(result["hints"]),
        "stats": {k: norm_num(v) for k, v in s.items() if k not in ("ttr", "tokenizer", "avg_sentence_len", "sentence_cvs")},
    }


def main() -> None:
    engine_js = ROOT / "web/engine.js"
    if not engine_js.exists():
        sys.exit("缺少 web/engine.js")
    if _HAS_JIEBA:
        print("提示:当前 Python 环境装有 jieba,统计对比忽略 ttr 字段(口径不同,无 TTR 判定规则,不受影响)")
    failed = False
    for profile in engine.available_profiles():
        rules_json = json.dumps(rules_to_json(profile), ensure_ascii=False)
        texts_json = json.dumps({n: t for n, t in PROBE_TEXTS}, ensure_ascii=False)
        script = ROOT / "_qa/_web_check.js"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        rules_path = ROOT / "_qa/_web_rules.json"
        texts_path = ROOT / "_qa/_web_texts.json"
        rules_path.write_text(rules_json, encoding="utf-8")
        texts_path.write_text(texts_json, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(script), str(engine_js.resolve()), str(rules_path), str(texts_path)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0:
            sys.exit(f"node 运行失败:\n{proc.stderr}")
        js_results = json.loads(proc.stdout)

        for name, _ in PROBE_TEXTS:
            py = engine.analyze(dict(PROBE_TEXTS)[name], profile)
            py_norm = normalize(
                {"findings": [f.to_dict() for f in py.findings],
                 "hints": [f.to_dict() for f in py.hints],
                 "stats": py.doc_stats.__dict__}
            )
            js_norm = normalize(js_results[name])
            if py_norm != js_norm:
                failed = True
                print(f"[FAIL] {profile}/{name}")
                for key in ("findings", "hints", "stats"):
                    if py_norm[key] != js_norm[key]:
                        print(f"  {key}:\n    py={json.dumps(py_norm[key], ensure_ascii=False)[:400]}"
                              f"\n    js={json.dumps(js_norm[key], ensure_ascii=False)[:400]}")
            else:
                print(f"[ok] {profile}/{name}")

    if failed:
        sys.exit("一致性检查未通过——两端实现已漂移,禁止发布")


if __name__ == "__main__":
    main()
