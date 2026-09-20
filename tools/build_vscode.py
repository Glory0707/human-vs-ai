"""构建 VS Code 扩展:注入规则 JSON + 同步引擎源码。

与 build_web.py 同模式——扩展不许独立演化:
- 规则 JSON 从同一 YAML 源生成(require tools/check_web_consistency.rules_to_json)
- engine.js 直接复制 web/engine.js(一致性测试背书的那个文件)

用法:python tools/build_vscode.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from human_vs_ai import __version__, engine  # noqa: E402
from tools.check_web_consistency import rules_to_json  # noqa: E402

EXT = ROOT / "vscode-extension"


def main() -> None:
    rules = {p: rules_to_json(p) for p in engine.available_profiles()}
    (EXT / "rules.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=1).replace("</", "<\\/>"),
        encoding="utf-8",
    )
    shutil.copyfile(ROOT / "web/engine.js", EXT / "engine.js")
    shutil.copyfile(ROOT / "web/rewrite.js", EXT / "rewrite.js")
    # package.json 版本与主包对齐
    pkg_path = EXT / "package.json"
    pkg = pkg_path.read_text(encoding="utf-8")
    import re
    pkg = re.sub(r'("version":\s*")[^"]+(")', rf"\g<1>{__version__}\g<2>", pkg, count=1)
    pkg_path.write_text(pkg, encoding="utf-8")
    print(f"已注入 {len(rules)} 个 profile 的规则并同步引擎 → {EXT}")


if __name__ == "__main__":
    main()
