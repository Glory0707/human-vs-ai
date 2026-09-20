"""Python 引擎与浏览器引擎(JS)的一致性守护。

双实现最大的风险是静默漂移——切分差一个字符、统计差一次舍入,
两端报告就会各说各话。本脚本用固定语料集对两端跑 analyze,
findings/hints 逐条 diff、stats 数值按 4 位小数 diff(ttr 已随
v0.11.0 口径统一为字级 2-gram,纳入对比;tokenizer/avg_sentence_len/
sentence_cvs 仅 Python 端存在,不比)。

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

PROBE_TEXTS = [
    ("ai_fixture", (ROOT / "tests/data/ai_academic.txt").read_text(encoding="utf-8")),
    ("human_fixture", (ROOT / "tests/data/human_academic.txt").read_text(encoding="utf-8")),
    ("empty", ""),
    ("codeblock_only", "```python\nprint(1)\n```"),
    ("no_punct", "字" * 600),
    ("mixed_md", "# 标题\n\n| a | b |\n|---|---|\n\n随着人工智能的快速发展，方法很多。首先，试了 A。其次，试了 B。\n\n综上所述，一切很好。\n"),
    ("quotes", '他说"这一点很重要。然后走了。"后来回来了。这是独句段测试。'),
    # 破折号连跑：数 run 数与数"对数+落单"在"———"上分歧，曾是真实漂移点
    ("triple_dash", "破折号用法研究开始了。———继续论述。第二句论述完整。第三句也完整。第四句更长一些。第五句正常表述。第六句继续。第七句收束。"),
    # Markdown 列表/编号：条目内容必须保留并参与分析（v0.8.0 前整块丢失）
    ("bullet_list", "# 标题\n\n- 首先要明确目标。\n- 其次要持续投入。\n- 综上所述，坚持才有回报。"),
    ("numbered_list", "1. 首先进行预处理。\n2. 其次进行训练。\n\n正文段，综上所述可行。"),
    # 表格：分隔行丢弃，单元格内容用逗号拼成句子保留
    ("table_row", "| 指标 | 数值 |\n|---|---|\n| 准确率显著提升 | 召回全面提高 |"),
    # 裸链接剥离，不污染字数与 4-gram；星号强调保留文字、不碰算式
    ("bare_url", "参考链接 https://example.com/a?x=1 该方法显著提升了性能。"),
    ("star_emph", "*重点*在于效率，3*5 也算，具有重要意义。"),
]

NODE_SCRIPT = r"""
const fs = require("fs");
const HvA = require(process.argv[2]);
const HvARewrite = require(process.argv[3]);
const rules = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const texts = JSON.parse(fs.readFileSync(process.argv[5], "utf8"));
const scoring = JSON.parse(fs.readFileSync(process.argv[6], "utf8"));
const out = {};
for (const [name, text] of Object.entries(texts)) {
  out[name] = HvA.analyze(text, rules, scoring);
}
const rw = {};
for (const [name, text] of Object.entries(texts)) {
  rw[name] = HvARewrite.rewriteText(text, rules);
}
process.stdout.write(JSON.stringify({ analyze: out, rewrite: rw }));
"""

# 改写器探针语料：覆盖每个判档分支（删/改/保留 × 腔调/功能说明/R1 数据保护）
REWRITE_PROBES = [
    "窗口一开，就是你的战场。",          # T2 抒情升华 → 改（截掉升华半句）
    "别急，代码明天还在仓库里。",        # T1 劝慰腔 → 改（截掉安慰半句）
    "研究显示，准确率从 0.71 提升到 0.89。",  # R1 数据保护 → 保留
    "自动匹配相关段落，一次最多三条。",  # T11 功能说明腔 → 整句删
    "点击右上角选择文件，支持批量导入。",  # T11 操作指引冗余 → 整句删
    "深夜的你，还在改这一版。",          # T7「X 的你」→ 改（拆框架）
    "本月共完成 3 次复盘，平均耗时 20 分钟。",  # R1 数据保护 → 保留
    "总而言之，这版更稳。",              # T12 收束保险腔 → 改（删收束词）
    "让每一次操作都得心应手",            # 无腔调命中 → 保留 + R3 提示
    "- 自动匹配相关段落，一次最多三条。",  # 行首列表符剥掉再判 → 仍整句删
    "你已经很棒了，不要给自己太大压力。",  # 通用关怀腔 → 整句删（不截残句）
    "已完成３轮内测。",                  # 全角数字 → 保留（R1：数字显式列全角，JS \d 不认全角）
]


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
            # 口味条目编号（personal profile 用）——网页要显示 T1…T12 标签
            "taste": r.taste,
            # JSON 不认 NaN;无阈值规则传 null
            "doc_threshold": None if r.doc_threshold != r.doc_threshold else r.doc_threshold,
            "doc_tiers": [
                [None if lim is None else lim, thr] for lim, thr in r.doc_tiers
            ],
        }
        for r in rules
    ]


def scoring_to_json(profile: str) -> dict | None:
    """评分模型的注入形态（scoring 段原样；未校准的 profile 为 None）。"""
    return engine.load_scoring(profile)


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

    score = result.get("score")
    norm_score = None
    if score:
        norm_score = {
            "index": round(float(score["index"]), 4),
            "components": {k: round(float(v), 4) for k, v in score["components"].items()},
            "corpus": score.get("corpus", ""),
            "human_p50": score.get("human_p50", 0),
            "human_p90": score.get("human_p90", 0),
        }

    return {
        "findings": fs(result["findings"]),
        "hints": fs(result["hints"]),
        "stats": {k: norm_num(v) for k, v in s.items() if k not in ("tokenizer", "avg_sentence_len", "sentence_cvs")},
        "score": norm_score,
        "score_note": result.get("score_note", ""),
    }


def main() -> None:
    engine_js = ROOT / "web/engine.js"
    rewrite_js = ROOT / "web/rewrite.js"
    if not engine_js.exists():
        sys.exit("缺少 web/engine.js")
    if not rewrite_js.exists():
        sys.exit("缺少 web/rewrite.js")
    failed = False
    for profile in engine.available_profiles():
        rules_json = json.dumps(rules_to_json(profile), ensure_ascii=False)
        probes = {n: t for n, t in PROBE_TEXTS}
        probes.update({f"rw{i}": t for i, t in enumerate(REWRITE_PROBES)})
        texts_json = json.dumps(probes, ensure_ascii=False)
        script = ROOT / "_qa/_web_check.js"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        rules_path = ROOT / "_qa/_web_rules.json"
        texts_path = ROOT / "_qa/_web_texts.json"
        scoring_path = ROOT / "_qa/_web_scoring.json"
        rules_path.write_text(rules_json, encoding="utf-8")
        texts_path.write_text(texts_json, encoding="utf-8")
        scoring_path.write_text(
            json.dumps(scoring_to_json(profile), ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(script), str(engine_js.resolve()), str(rewrite_js.resolve()),
             str(rules_path), str(texts_path), str(scoring_path)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0:
            sys.exit(f"node 运行失败:\n{proc.stderr}")
        payload = json.loads(proc.stdout)
        js_results = payload["analyze"]
        js_rewrite = payload["rewrite"]

        for name, _ in PROBE_TEXTS:
            py = engine.analyze(dict(PROBE_TEXTS)[name], profile)
            score = py.score
            py_norm = normalize(
                {"findings": [f.to_dict() for f in py.findings],
                 "hints": [f.to_dict() for f in py.hints],
                 "stats": py.doc_stats.__dict__,
                 "score": ({"index": score.index, "components": score.components,
                            "corpus": score.corpus, "human_p50": score.human_p50,
                            "human_p90": score.human_p90} if score else None),
                 "score_note": py.scoring_note}
            )
            js_norm = normalize(js_results[name])
            if py_norm != js_norm:
                failed = True
                print(f"[FAIL] {profile}/{name}")
                for key in ("findings", "hints", "stats", "score", "score_note"):
                    if py_norm[key] != js_norm[key]:
                        print(f"  {key}:\n    py={json.dumps(py_norm[key], ensure_ascii=False)[:400]}"
                              f"\n    js={json.dumps(js_norm[key], ensure_ascii=False)[:400]}")
            else:
                print(f"[ok] {profile}/{name}")

        # 改写器一致性：动作/候选/方向/理由逐字段 diff（两端不许独立演化）
        from human_vs_ai.rewrite import rewrite_text
        for i, text in enumerate(REWRITE_PROBES):
            keys = ("text", "action", "candidate", "direction", "reason", "taste")
            py_rw = [{k: a.to_dict()[k] for k in keys}
                     for a in rewrite_text(text, profile).advices]
            js_rw = [{k: a[k] for k in keys} for a in js_rewrite[f"rw{i}"]["advices"]]
            if py_rw != js_rw:
                failed = True
                print(f"[FAIL] {profile}/rewrite#{i} {text[:24]}")
                print(f"    py={json.dumps(py_rw, ensure_ascii=False)[:300]}"
                      f"\n    js={json.dumps(js_rw, ensure_ascii=False)[:300]}")
            else:
                print(f"[ok] {profile}/rewrite#{i}")

    if failed:
        sys.exit("一致性检查未通过——两端实现已漂移,禁止发布")


if __name__ == "__main__":
    main()
