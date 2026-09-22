"""构建 Obsidian 插件：注入规则 JSON + 引擎源码 + 版本号。

与 build_web/build_vscode 同模式——插件端不许独立演化：
- main.js 由 main.template.js 注入 engine/rewrite/render 与规则 JSON 生成
- manifest.json / versions.json 由主包版本生成（id: human-vs-ai）
- styles.css 从源码目录原样复制

用法:python tools/build_obsidian.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import __version__, engine  # noqa: E402
from tools.build_web import _inline_safe, _strip_calibration_notes  # noqa: E402
from tools.check_web_consistency import rules_to_json, scoring_to_json  # noqa: E402

PLUGIN = ROOT / "obsidian-plugin"
MIN_APP_VERSION = "1.4.0"


def main() -> None:
    template = (PLUGIN / "main.template.js").read_text(encoding="utf-8")
    engine_js = (ROOT / "web/engine.js").read_text(encoding="utf-8")
    rewrite_js = (ROOT / "web/rewrite.js").read_text(encoding="utf-8")
    render_js = (ROOT / "web/render.js").read_text(encoding="utf-8")
    rules = {
        p: _strip_calibration_notes(rules_to_json(p))
        for p in engine.available_profiles()
    }
    scoring = {p: scoring_to_json(p) for p in engine.available_profiles()}

    main_js = (
        template
        .replace("__ENGINE__", _inline_safe(engine_js))
        .replace("__REWRITE__", _inline_safe(rewrite_js))
        .replace("__RENDER__", _inline_safe(render_js))
        .replace("__RULES_JSON__", _inline_safe(json.dumps(rules, ensure_ascii=False)))
        .replace("__SCORING_JSON__", _inline_safe(json.dumps(scoring, ensure_ascii=False)))
        .replace("__VERSION__", __version__)
    )
    for leftover in ("__RULES_JSON__", "__SCORING_JSON__", "__ENGINE__",
                     "__REWRITE__", "__RENDER__", "__VERSION__"):
        assert leftover not in main_js, f"占位符未替换:{leftover}"
    (PLUGIN / "main.js").write_text(main_js, encoding="utf-8")

    manifest = {
        "id": "human-vs-ai",
        "name": "Human vs AI 中文 AI 味分析",
        "version": __version__,
        "minAppVersion": MIN_APP_VERSION,
        "description": "可解释的中文 AI 味分析——指出笔记里哪里像模板、为什么、怎么改。风格提示，不是 AI 判定。纯本地运行。",
        "author": "Glory0707",
        "isDesktopOnly": True,
    }
    (PLUGIN / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (PLUGIN / "versions.json").write_text(
        json.dumps({__version__: MIN_APP_VERSION}, indent=2), encoding="utf-8")

    n_scored = sum(1 for v in scoring.values() if v)
    print(f"已生成 {PLUGIN}/main.js（{len(rules)} 个 profile，{n_scored} 个带评分模型）"
          f"+ manifest.json v{__version__}")


if __name__ == "__main__":
    main()
