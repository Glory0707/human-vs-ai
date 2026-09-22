"""构建网页版:模板 + 引擎源码 + 规则 JSON → 单文件 web/index.html。

规则与引擎永远从仓库源码注入,网页版不允许独立演化——
改了 YAML 或 engine.js 之后必须重跑本脚本,CI 由
tools/check_web_consistency.py 守护两端一致性。

内联安全:HTML5 里只有 "</script" 会提前终结脚本块,所以只需转义它。
不能全量替换 "</"——render.js 的 esc() 里有正则 /</g,替换成 /<\\/g
会把正则字面量打碎(首测浏览器控制台实录)。

用法:python tools/build_web.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import __version__, engine  # noqa: E402
from tools.check_web_consistency import rules_to_json, scoring_to_json  # noqa: E402


def _inline_safe(js: str) -> str:
    return re.sub(r"</(script)", r"<\\/\1", js, flags=re.I)


def _strip_calibration_notes(rules: list[dict]) -> list[dict]:
    """前端构建(web + vscode)剥离规则解释里的校准注——维护者信息不进
    用户界面(界面减法)。CLI 的 explain 保留完整解释;findings 结构
    不受影响(一致性测试不比较 explanation 字段)。
    """
    MARKS = ("校准注（", "走查校准（", "注：C-ReD")
    for r in rules:
        cut = len(r["explanation"])
        for m in MARKS:
            idx = r["explanation"].find(m)
            if 0 <= idx < cut:
                cut = idx
        # YAML folded 块把换行折叠成空格,按标记切而非按行
        r["explanation"] = r["explanation"][:cut].rstrip(" 。;；—-")
    return rules


def main() -> None:
    template = (ROOT / "web/template.html").read_text(encoding="utf-8")
    engine_js = (ROOT / "web/engine.js").read_text(encoding="utf-8")
    rewrite_js = (ROOT / "web/rewrite.js").read_text(encoding="utf-8")
    render_js = (ROOT / "web/render.js").read_text(encoding="utf-8")
    rules = {
        p: _strip_calibration_notes(rules_to_json(p))
        for p in engine.available_profiles()
    }
    rules_json = _inline_safe(json.dumps(rules, ensure_ascii=False))
    scoring_json = _inline_safe(
        json.dumps({p: scoring_to_json(p) for p in engine.available_profiles()},
                   ensure_ascii=False))

    html = (
        template.replace("__VERSION__", __version__)
        .replace("__ENGINE__", _inline_safe(engine_js))
        .replace("__REWRITE__", _inline_safe(rewrite_js))
        .replace("__RENDER__", _inline_safe(render_js))
        .replace("__RULES_JSON__", rules_json)
        .replace("__SCORING_JSON__", scoring_json)
    )
    out = ROOT / "web/index.html"
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out}（{out.stat().st_size // 1024} KB,profiles: {', '.join(rules)}）")


if __name__ == "__main__":
    main()
