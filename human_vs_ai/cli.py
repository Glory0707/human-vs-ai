"""命令行入口。

五个子命令：
  check     分析文件（主命令）
  rewrite   按个人口味给逐句改写建议（删/改/保留）
  stats     只看统计特征（调阈值/做研究用）
  explain   打印一条规则的完整说明（报告里看到 ID 想深究时用）
  profiles  列出可用场景
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, engine, report, rewrite


def _read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        sys.exit(f"错误：文件不存在 {p}")
    if p.is_dir():
        sys.exit(f"错误：{p} 是目录，请传入文本文件")
    try:
        return p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return p.read_text(encoding="gb18030", errors="replace")
    except OSError as e:
        sys.exit(f"错误：无法读取 {p}（{e.strerror}）")


_PROFILE_DESC = {
    "academic": ("学术", "论文、摘要、实验报告"),
    "general": ("问答", "知乎、公众号、科普"),
    "official": ("公文", "通知、意见、实施方案"),
    "personal": ("我的口味", "短文案：界面文案、标题、提示语（rewrite 专用）"),
}


def _require_profile(name: str) -> None:
    """未知场景给干净报错，不抛 traceback。"""
    if name not in engine.available_profiles():
        sys.exit(
            f"错误：场景 '{name}' 不存在。可用：{', '.join(engine.available_profiles())}"
        )


def _emit(out: str, output: str | None) -> None:
    """结果出口：写文件（提示走 stderr，不污染管道）或打印。"""
    if output:
        Path(output).write_text(out, encoding="utf-8")
        print(f"已写入 {output}", file=sys.stderr)
    else:
        print(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="human-vs-ai",
        description="可解释的中文 AI 味分析器——指出哪里像模板、为什么、怎么改。不给 AI 概率。",
    )
    parser.add_argument("--version", action="version", version=f"human-vs-ai {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="分析文本文件")
    p_check.add_argument("file", help="txt / md 文件；或 - 从标准输入读")
    p_check.add_argument("-p", "--profile", default="academic", help="场景（默认 academic）")
    p_check.add_argument("-f", "--format", default="terminal", choices=["terminal", "md", "json"])
    p_check.add_argument("-o", "--output", help="写入文件（默认打印）")
    p_check.add_argument(
        "--min-severity",
        default="hint",
        choices=["high", "medium", "low", "hint"],
        help="报告的最低严重级（默认全量）",
    )

    p_stats = sub.add_parser("stats", help="只打印全文统计特征（JSON）")
    p_stats.add_argument("file", help="txt / md 文件；或 - 从标准输入读")
    p_stats.add_argument("-p", "--profile", default="academic")

    p_rw = sub.add_parser(
        "rewrite",
        help="按个人口味给逐句改写建议（删/改/保留三档，personal profile）",
    )
    p_rw.add_argument("file", help="txt / md 文件；或 - 从标准输入读")
    p_rw.add_argument("-p", "--profile", default="personal")
    p_rw.add_argument("-f", "--format", default="terminal", choices=["terminal", "json"])
    p_rw.add_argument("-o", "--output", help="写入文件（默认打印）")

    sub.add_parser("profiles", help="列出可用场景")

    p_explain = sub.add_parser("explain", help="打印一条规则的完整说明")
    p_explain.add_argument("rule_id")
    p_explain.add_argument("-p", "--profile", default="academic")

    args = parser.parse_args(argv)

    if args.command == "profiles":
        for name in engine.available_profiles():
            label, desc = _PROFILE_DESC.get(name, ("", ""))
            print(f"{name} · {label} — {desc}" if label else name)
        return

    if args.command == "explain":
        _require_profile(args.profile)
        for rule in engine.load_rules(args.profile):
            if rule.id.upper() == args.rule_id.upper():
                parts = [
                    f"{rule.id} {rule.name}",
                    f"层级：{rule.tier} · 严重级：{rule.severity} · 范围：{rule.scope}",
                    "",
                    rule.explanation,
                ]
                if rule.suggestion:
                    parts += ["", f"建议：{rule.suggestion}"]
                if rule.example_before:
                    parts += ["", f"例：{rule.example_before}", f"改：{rule.example_after}"]
                if rule.references:
                    parts += ["", "出处：", *[f"  {u}" for u in rule.references]]
                print("\n".join(parts))
                return
        sys.exit(f"规则不存在：{args.rule_id}（profile={args.profile}）")

    if args.command == "rewrite":
        _require_profile(args.profile)
        text = sys.stdin.read() if args.file == "-" else _read_file(args.file)
        result = rewrite.rewrite_text(text, args.profile)
        if args.format == "json":
            out = json.dumps(
                {"tool": "human-vs-ai", "version": __version__, "profile": result.profile,
                 "advices": [a.to_dict() for a in result.advices]},
                ensure_ascii=False, indent=2)
        else:
            out = rewrite.render_advice(result)
        _emit(out, args.output)
        return

    _require_profile(args.profile)
    text = sys.stdin.read() if args.file == "-" else _read_file(args.file)
    result = engine.analyze(text, args.profile)

    if args.command == "stats":
        # 契约是"只看统计特征"：只出 stats，不夹带 findings
        print(json.dumps(
            {"tool": "human-vs-ai", "version": __version__,
             "profile": result.profile, "stats": result.doc_stats.to_dict()},
            ensure_ascii=False, indent=2))
        return

    if args.min_severity != "hint":
        floor = engine.SEVERITY_ORDER[args.min_severity]
        result.findings = [f for f in result.findings if engine.SEVERITY_ORDER[f.severity] >= floor]
        if floor > 0:
            result.hints = []

    out = report.render(result, args.format)
    _emit(out, args.output)


if __name__ == "__main__":
    main()
