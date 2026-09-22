# -*- coding: utf-8 -*-
"""对抗性输入 Py/JS 双端对拍：找一致性探针没覆盖的真实漂移。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from human_vs_ai import engine  # noqa: E402

PROBES = {
    "empty": "",
    "spaces": "   \n\n  \t ",
    "punct_only": "。。。。。。！！！？？？",
    "exclaim": "好！！！好！！！好！！！好！！！",
    "ellipsis_mix": "先这样…然后……最后呢。",
    "unbalanced_dquote": '他说"这一点很关键。然后走了。后来又回来了。最终确认无误。',
    "nested_cjk_quote": "外层“内层「更深。还没有完」继续”结束。第二句。",
    "table_double_pipe": "| a || b |\n|---|---|\n|| x || y |",
    "table_no_pipes": "| 单列 |\n|---|\n| 内容。 |",
    "pipe_in_text": "竖线|在正文中|不算表格。第二句正常。",
    "setext_underline": "标题文字\n===\n\n正文开始。第二句。",
    "hr_variants": "---\n\n***\n\n___\n\n正文。第二句。",
    "checkbox": "- [ ] 未完成项\n- [x] 已完成项\n- [ ] ",
    "list_marker_only": "- \n* \n+ 1\n1. \n2、",
    "decimal_list": "3.14 是圆周率。2.718 是自然常数。",
    "ordered_paren": "1) 第一项内容\n2) 第二项内容",
    "crlf": "第一段第一句。\r\n第一段第二句。\r\n\r\n第二段唯一句。",
    "emoji_only": "😀😃😄 🀄🈚🈶",
    "emoji_mixed": "这个方法真的绝了😀！效果拔群🚀🚀。第二句正常表述。",
    "rtl_mixed": "正文 English mixed مع العربية 文本。第二句。",
    "fullwidth_punct": "全角标点！疑问？句号。顿号、分号；冒号：",
    "long_dash_run": "甲——乙———丙————丁。第二句。",
    "asterisk_emph": "*强调*与**粗体**与***加粗斜体***。普通句子。",
    "asterisk_math": "3*5=15，2**3=8，公式*不*该被剥。第二句。",
    "asterisk_flood": "*" * 200,
    "backtick_flood": "`" * 100,
    "unclosed_fence": "```python\nprint(1)\n\n正文被代码块吞掉了吗。第二句。",
    "tilde_fence": "~~~\ncode\n~~~\n\n正文。第二句。",
    "quote_line": "> 引用行内容。第二句在引用外。",
    "nested_quote_line": "> > 深层引用。内容如何？",
    "html_comment": "<!-- 注释 -->正文。<!-- x --> 第二句。",
    "inline_html": "<b>加粗</b>正文。第二句。",
    "link_bare": "[文字](https://x.com/a)与https://y.com/b混排。第二句。",
    "image": "![图片](https://x.com/i.png)前后文字。第二句。",
    "nbsp": "全角空格　与普通空格 混排。第二句。",
    "digits_only": "1234567890 9876543210 000",
    "latin_only": "The quick brown fox jumps over the lazy dog. Again and again.",
    "cjk_ext": "𠮷野家与𠀋𠂉生僻字。第二句。",
    "one_sentence_40": "这是一个刚好四十字左右的单句总结段用来触发独句段形状规则的测试句子测试。",
    "quote_wrap_all": "“全部内容都在引号里。包括第二句吗？”",
    "zero_width": "零宽\u200b字符\u200b混入。第二句。",
    "nul_ish": "控制字符\u0007混入\u001b[31m。第二句。",
    "very_long_sent": "长" * 3000 + "。",
    "many_sentences": "短句。" * 500,
    "md_heading_setext_mix": "# 一级\n\nsetext\n-----\n\n正文。再一句。",
    "table_align": "| 左 | 中 |右|\n|:---|:---:|---:|\n| a | b | c |",
    "trailing_spaces": "句子后有空格。   \n\n  段首有空格。再句。",
    "bold_list": "- **首先**要明确目标。\n- *其次*要持续投入。",
    # ---- 下排：对拍实证过的漂移类（修复后固化为回归）----
    # Python splitlines 行界全集：\u2028 等在段中也要拆行再 \n 拼接
    "line_sep_mid": "随着人工智能的快速发展。\u2028综上所述，该方法具有重要意义。此外还需验证。与此同时保持完整。最后总结收束。",
    "lone_cr_mid": "随着人工智能的快速发展。\r综上所述，该方法具有重要意义。此外还需验证。与此同时保持完整。最后总结收束。",
    # 边界标点后的闭引号并入本句（句长分布随之不同）
    "curly_close_unbalanced": "结论如此。”下一句话。最后再确认一次边界无误。这一句是第四句。第五句总结。",
    # 孤立低代理：Python len 算 1 码点，JS 旧版 cpLength 漏数
    "lone_low_surrogate": "测试\udc00文本。第二句正常表述。第三句也是正常的。",
    # 40 个扩展区码点恰好踩独句段阈值（cpLength 配对计数回归）
    "astral_oneliner": "\U0001F600" * 40,
}


rows = {}
for name, text in PROBES.items():
    r = engine.analyze(text, "academic")
    sc = r.score
    rows[name] = {
        "findings": [f.to_dict() for f in r.findings],
        "hints": [f.to_dict() for f in r.hints],
        "stats": r.doc_stats.to_dict(),
        "score": ({"index": sc.index, "components": sc.components,
                   "corpus": sc.corpus, "human_p50": sc.human_p50,
                   "human_p90": sc.human_p90} if sc else None),
        "score_note": r.scoring_note,
    }
# 孤立代理进不了 ensure_ascii=False 的 UTF-8 文件——探针/产物一律转义通道
Path("_qa/_drift_py.json").write_text(json.dumps(rows, ensure_ascii=True), encoding="utf-8")

js_probe = Path("_qa/_drift_js.js")
texts_json = json.dumps(PROBES, ensure_ascii=True)
js_probe.write_text(f'''
const HvA = require("{(ROOT / "web/engine.js").as_posix()}");
const RULES = require("{(ROOT / "_qa/_drift_rules.json").as_posix()}");
const SCORING = require("{(ROOT / "_qa/_drift_scoring.json").as_posix()}");
const texts = {texts_json};
const out = {{}};
for (const [name, text] of Object.entries(texts)) {{
  const r = HvA.analyze(text, RULES.academic, SCORING || null);
  out[name] = r;
}}
// 孤立代理（无配对的高/低代理项）转义回 \\uXXXX 再出管道；成对代理保持原样
const LONE_HI = /[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])/g;
const LONE_LO = /(?<![\\uD800-\\uDBFF])[\\uDC00-\\uDFFF]/g;
const escS = t => t.replace(LONE_HI, c => "\\\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"))
                 .replace(LONE_LO, c => "\\\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
console.log(escS(JSON.stringify(out)));
''', encoding="utf-8")

sys.path.insert(0, str(ROOT / "tools"))
from check_web_consistency import rules_to_json, scoring_to_json  # noqa: E402
Path("_qa/_drift_rules.json").write_text(
    json.dumps({"academic": rules_to_json("academic")}, ensure_ascii=False), encoding="utf-8")
Path("_qa/_drift_scoring.json").write_text(json.dumps(scoring_to_json("academic"), ensure_ascii=False), encoding="utf-8")

proc = subprocess.run(["node", str(js_probe)], capture_output=True, text=True, encoding="utf-8")
if proc.returncode != 0:
    sys.exit(f"node 失败:\n{proc.stderr}")
js = json.loads(proc.stdout)
py = json.loads(Path("_qa/_drift_py.json").read_text(encoding="utf-8"))


def norm_num(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return v


fails = 0
for name in PROBES:
    p, j = py[name], js[name]
    pf = json.dumps(p["findings"], ensure_ascii=True, sort_keys=True)
    jf = json.dumps(j["findings"], ensure_ascii=True, sort_keys=True)
    ph = json.dumps(p["hints"], ensure_ascii=True, sort_keys=True)
    jh = json.dumps(j["hints"], ensure_ascii=True, sort_keys=True)
    diffs = []
    if pf != jf:
        diffs.append(f"  findings:\n    py={pf[:300]}\n    js={jf[:300]}")
    if ph != jh:
        diffs.append(f"  hints:\n    py={ph[:200]}\n    js={jh[:200]}")
    for k in ("n_paragraphs", "n_sentences", "n_chars", "sentence_cv", "para_len_cv",
              "ttr", "conn_density", "ngram_repeat", "dash_density", "avg_sentence_len"):
        pv, jv = norm_num(p["stats"].get(k)), norm_num(j["stats"].get(k))
        if pv != jv:
            diffs.append(f"  stats.{k}: py={pv} js={jv}")
    if p["score_note"] != j.get("score_note", ""):
        diffs.append(f"  score_note: py={p['score_note']!r} js={j.get('score_note')!r}")
    if (p["score"] is None) != (j["score"] is None):
        diffs.append(f"  score presence: py={p['score'] is not None} js={j['score'] is not None}")
    elif p["score"]:
        pi, ji = round(p["score"]["index"], 4), round(j["score"]["index"], 4)
        if pi != ji:
            diffs.append(f"  score.index: py={pi} js={ji}")
        pc = {k: norm_num(v) for k, v in p["score"]["components"].items()}
        jc = {k: norm_num(v) for k, v in j["score"]["components"].items()}
        if pc != jc:
            diffs.append(f"  score.components: py={pc} js={jc}")
    if diffs:
        fails += 1
        print(f"[DRIFT] {name}")
        for d in diffs:
            print(d)
print(f"\n{len(PROBES)} 个探针，{fails} 个漂移" + ("，全部一致 ✓" if not fails else ""))
Path("_qa/_drift_js.js").unlink()
Path("_qa/_drift_rules.json").unlink()
Path("_qa/_drift_scoring.json").unlink()
Path("_qa/_drift_py.json").unlink()
