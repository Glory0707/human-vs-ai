"""命令行入口。

七个子命令：
  check     分析文件（主命令；目录/glob 走批量汇总）
  diff      改前改后对比——验证修改有没有效
  collect   导出脱敏校准样本（自愿提交，帮词表进化）
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

import yaml

from . import __version__, batch, collect, diff, engine, htreport, readers, report, rewrite, sarif


def _read_file(path: str) -> str:
    try:
        return readers.read_text(path)
    except FileNotFoundError:
        sys.exit(f"错误：文件不存在 {path}")
    except IsADirectoryError:
        sys.exit(f"错误：{path} 是目录，请传入文本文件")
    except ValueError as e:
        sys.exit(f"错误：{e}")
    except OSError as e:
        sys.exit(f"错误：无法读取 {path}（{e.strerror}）")


_PROFILE_DESC = {
    "academic": ("学术", "论文、摘要、实验报告"),
    "general": ("问答", "知乎、公众号、科普"),
    "official": ("公文", "通知、意见、实施方案"),
    "personal": ("我的口味", "界面文案、标题、提示语"),
    "news": ("新闻", "新闻报道、资讯、通稿"),
    "essay": ("作文", "高考作文、议论文、考场写作"),
    "review": ("短评", "影评、书评、商品点评"),
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
        try:
            Path(output).write_text(out, encoding="utf-8")
        except OSError as e:
            sys.exit(f"错误：无法写入 {output}（{e.strerror}）")
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

    p_check = sub.add_parser("check", help="分析文本文件（目录/glob 走批量汇总）")
    p_check.add_argument("file", help="txt/md/docx/odt 文件、目录、glob；或 - 从标准输入读")
    p_check.add_argument("-p", "--profile", default="academic", help="场景（默认 academic）")
    p_check.add_argument(
        "-f", "--format", default="terminal",
        choices=["terminal", "md", "json", "sarif", "csv", "html"],
        help="出口格式（csv/sarif 需文件输入）")
    p_check.add_argument("-o", "--output", help="写入文件（默认打印）")
    p_check.add_argument(
        "--min-severity", default="hint", choices=["high", "medium", "low", "hint"],
        help="报告的最低严重级（默认全量；仅单文件出口生效）")
    p_check.add_argument(
        "--fail-above", type=float, default=None, metavar="N",
        help="AI 味指数超过 N 时退出码 1（CI 门禁）")

    p_diff = sub.add_parser("diff", help="改前改后对比")
    p_diff.add_argument("old", help="改前文件")
    p_diff.add_argument("new", help="改后文件")
    p_diff.add_argument("-p", "--profile", default="academic")
    p_diff.add_argument("-f", "--format", default="terminal", choices=["terminal", "md", "json"])
    p_diff.add_argument("-o", "--output", help="写入文件（默认打印）")

    p_stats = sub.add_parser("stats", help="只打印全文统计特征（JSON）")
    p_stats.add_argument("file", help="txt/md/docx/odt 文件；或 - 从标准输入读")
    p_stats.add_argument("-p", "--profile", default="academic")

    p_rw = sub.add_parser(
        "rewrite",
        help="按个人口味给逐句改写建议（删/改/保留三档，personal profile）",
    )
    p_rw.add_argument("file", help="txt/md/docx/odt 文件；或 - 从标准输入读")
    p_rw.add_argument("-p", "--profile", default="personal")
    p_rw.add_argument("-f", "--format", default="terminal", choices=["terminal", "json"])
    p_rw.add_argument("-o", "--output", help="写入文件（默认打印）")

    sub.add_parser("profiles", help="列出可用场景")

    p_col = sub.add_parser(
        "collect",
        help="导出脱敏校准样本（自愿提交）")
    p_col.add_argument("file", help="txt/md/docx/odt 文件；或 - 从标准输入读")
    p_col.add_argument(
        "-p", "--profile", default="academic",
        help="当时使用的场景（默认 academic）")
    p_col.add_argument(
        "--label", required=True, choices=["miss", "fp", "hit"],
        help="miss=漏报 / fp=误报 / hit=判定准确")
    p_col.add_argument("-o", "--output", help="写入 JSONL 文件（默认打印）")

    p_explain = sub.add_parser("explain", help="打印一条规则的完整说明")
    p_explain.add_argument("rule_id")
    p_explain.add_argument("-p", "--profile", default="academic")

    args = parser.parse_args(argv)

    # 规则库被改坏时给可读的报错，不抛 yaml traceback；
    # 其余未预期异常照常抛——不能把真 bug 吞成一句话
    try:
        _dispatch(args)
    except yaml.YAMLError as e:
        sys.exit(f"错误：规则库 YAML 解析失败：{str(e).strip().splitlines()[0]}")


def _dispatch(args: argparse.Namespace) -> None:
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

    if args.command == "collect":
        _require_profile(args.profile)
        text = sys.stdin.read() if args.file == "-" else _read_file(args.file)
        if not text.strip():
            sys.exit("错误：文件为空，没有可导出的样本")
        sample = collect.build_sample(text, args.profile, args.label)
        out = collect.render_jsonl([sample])
        if args.output:
            _emit(out, args.output)
        else:
            sys.stdout.write(out)
            print(f"已导出 1 条样本（{collect.LABELS[args.label]}），提交方式见 README。",
                  file=sys.stderr)
        return

    if args.command == "diff":
        _require_profile(args.profile)
        old_text = _read_file(args.old)
        new_text = _read_file(args.new)
        d = diff.compute(old_text, new_text, args.profile)
        _emit(diff.render(d, args.format), args.output)
        return

    _require_profile(args.profile)
    if args.command == "stats":
        text = sys.stdin.read() if args.file == "-" else _read_file(args.file)
        result = engine.analyze(text, args.profile)
        # 契约是"只看统计特征"：只出 stats，不夹带 findings
        print(json.dumps(
            {"tool": "human-vs-ai", "version": __version__,
             "profile": result.profile, "stats": result.doc_stats.to_dict()},
            ensure_ascii=False, indent=2))
        return
    _check(args)


def _analyze_file(path: Path, profile: str):
    try:
        text = readers.read_text(str(path))
    except FileNotFoundError:
        sys.exit(f"错误：文件不存在 {path}")
    except ValueError as e:
        sys.exit(f"错误：{e}")
    except OSError as e:
        sys.exit(f"错误：无法读取 {path}（{e.strerror}）")
    return text, engine.analyze(text, profile)


def _gate(result: engine.AnalysisResult, where: str, threshold: float) -> None:
    """--fail-above：超阈值走 stderr 提示 + 退出码 1，不污染正常出口。"""
    if result.score and result.score.index > threshold:
        print(f"human-vs-ai：指数 {result.score.index:.0f} > {threshold:g}（{where}）",
              file=sys.stderr)
        sys.exit(1)


def _check(args: argparse.Namespace) -> None:
    paths = batch.resolve_paths(args.file)

    if paths is None:  # stdin：单文件管道
        if args.format in ("sarif", "csv"):
            sys.exit(f"错误：{args.format} 输出需要文件输入，管道不支持")
        if args.format == "html":
            sys.exit("错误：html 报告暂只支持文件输入；管道用 terminal/md/json")
        text = sys.stdin.read()
        result = engine.analyze(text, args.profile)
        _apply_min_severity(result, args)
        _emit(report.render(result, args.format), args.output)
        return

    if len(paths) == 1:
        text, result = _analyze_file(paths[0], args.profile)
        _apply_min_severity(result, args)
        if args.format == "sarif":
            out = sarif.render([(str(paths[0]), text, result)], args.profile)
        elif args.format == "html":
            out = htreport.render_html(result)
        else:
            out = report.render(result, args.format)
        _emit(out, args.output)
        if args.fail_above is not None:
            _gate(result, str(paths[0]), args.fail_above)
        return

    if args.format == "html":
        sys.exit("错误：html 报告仅支持单文件；批量扫描用 terminal/csv/json")

    if not paths:
        if Path(args.file).is_dir():
            sys.exit("错误：目录中没有可分析的文件"
                     f"（支持 {'/'.join(readers.SCAN_EXTS)}）：{args.file}")
        sys.exit(f"错误：没有匹配到可分析的文件：{args.file}")

    entries, rows = [], []
    for p in paths:
        try:
            text = readers.read_text(str(p))
        except (ValueError, OSError) as e:
            print(f"跳过 {p}：{e}", file=sys.stderr)
            continue
        result = engine.analyze(text, args.profile)
        entries.append((str(p), text, result))
        rows.append(batch.summarize(p, result))
    if not entries:
        sys.exit(f"错误：所有文件都读取失败：{args.file}")

    rows = batch.sort_rows(rows)
    if args.format == "sarif":
        out = sarif.render(entries, args.profile)
    else:
        out = batch.render(rows, args.profile, args.format)
    _emit(out, args.output)
    if args.fail_above is not None:
        for r in rows:
            if r["index"] is not None and r["index"] > args.fail_above:
                print(f"human-vs-ai：指数 {r['index']} > {args.fail_above:g}（{r['file']}）",
                      file=sys.stderr)
                sys.exit(1)


def _apply_min_severity(result: engine.AnalysisResult, args: argparse.Namespace) -> None:
    if args.min_severity == "hint":
        return
    floor = engine.SEVERITY_ORDER[args.min_severity]
    result.findings = [f for f in result.findings
                       if engine.SEVERITY_ORDER[f.severity] >= floor]
    if floor > 0:
        result.hints = []


if __name__ == "__main__":
    main()
