"""构建网页版:模板 + 引擎源码 + 规则 JSON → 单文件 web/index.html。

规则与引擎永远从仓库源码注入,网页版不允许独立演化——
改了 YAML 或 engine.js 之后必须重跑本脚本,CI 由
tools/check_web_consistency.py 守护两端一致性。

用法:python tools/build_web.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import __version__, engine  # noqa: E402
from tools.check_web_consistency import rules_to_json  # noqa: E402


def _strip_calibration_notes(rules: list[dict]) -> list[dict]:
    """网页版剥离规则解释里的校准注——那是维护者信息,不是用户信息(界面减法)。

    CLI 的 explain 保留完整解释;两端 findings 结构不受影响(一致性
    测试不比较 explanation 字段)。
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
    rules = {
        p: _strip_calibration_notes(rules_to_json(p))
        for p in engine.available_profiles()
    }
    rules_json = json.dumps(rules, ensure_ascii=False).replace("</", "<\\/")

    html = (
        template.replace("__VERSION__", __version__)
        .replace("__ENGINE__", engine_js.replace("</", "<\\/"))
        .replace("__REWRITE__", rewrite_js.replace("</", "<\\/"))
        .replace("__RULES_JSON__", rules_json)
    )
    out = ROOT / "web/index.html"
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out}（{out.stat().st_size // 1024} KB,profiles: {', '.join(rules)}）")


if __name__ == "__main__":
    main()
