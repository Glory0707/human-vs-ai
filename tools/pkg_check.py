"""PyPI 发布自查：构建 → 产物内容审计 → 元数据 → 干净 venv 安装冒烟。

发布前唯一入口检查，任何一步失败即退出码 1：
  1. python -m build 重新构建 sdist+wheel
  2. wheel 内容审计（7 份规则 YAML、入口脚本、METADATA 关键字段、无杂物）
  3. sdist 内容审计（源码、规则、pyproject、LICENSE、README）
  4. twine check 元数据校验
  5. 临时 venv 装 wheel：CLI 冒烟（profiles/check/stats/explain/ppl 缺依赖指引）
  6. 同 venv 改装 sdist：验证从源码分发包重建的完整路径

用法：python tools/pkg_check.py（需网络装依赖；twine 缺失时报安装指引）
"""
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from human_vs_ai import __version__  # noqa: E402
DIST = ROOT / "dist"
RULES = ("academic", "essay", "general", "news", "official", "personal", "review")
JUNK = ("__pycache__", ".pyc", "_qa/", "tests/")
# 合成样例：只做冒烟（出口非空、无崩溃），不断言命中数
FIXTURE = ("在数字化时代，随着人工智能技术的飞速发展，各行各业迎来了前所未有的机遇与挑战。"
           "首先，技术进步提高了生产效率；其次，管理模式随之革新；"
           "最后，综上所述，只有拥抱变化才能行稳致远。")
failures: list[str] = []


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"（{detail}）" if detail and not cond else ""))
    if not cond:
        failures.append(f"{name}: {detail}")


def build() -> tuple[Path, Path]:
    print("[1/6] 构建 sdist+wheel")
    import shutil
    shutil.rmtree(DIST, ignore_errors=True)
    r = run([sys.executable, "-m", "build"], cwd=ROOT, timeout=300)
    if r.returncode != 0:
        sys.exit(f"构建失败：\n{r.stderr[-2000:]}")
    if "deprecat" in (r.stdout + r.stderr).lower():
        failures.append("构建输出含弃用警告")
        print(r.stdout[-800:], r.stderr[-800:])
    wheel = next(DIST.glob("*.whl"), None)
    sdist = next(DIST.glob("*.tar.gz"), None)
    ok("产物齐全", wheel is not None and sdist is not None)
    return wheel, sdist


def audit_wheel(wheel: Path) -> None:
    print("[2/6] wheel 内容审计")
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        meta = z.read([n for n in names if n.endswith(".dist-info/METADATA")][0]
                      ).decode("utf-8")
        ep = z.read([n for n in names if n.endswith("entry_points.txt")][0]).decode("utf-8")
    tops = {n.split("/")[0] for n in names}
    infos = {t for t in tops if t.endswith(".dist-info")}
    ok("仅包目录+dist-info", tops - infos == {"human_vs_ai"} and len(infos) == 1,
       str(tops))
    for rule in RULES:
        ok(f"rules/{rule}.yaml", f"human_vs_ai/rules/{rule}.yaml" in names)
    ok("入口脚本", "human-vs-ai" in ep and "cli:main" in ep)
    ok("无杂物", not any(j in n for n in names for j in JUNK),
       str([n for n in names for j in JUNK if j in n][:3]))
    ok("License-Expression", "License-Expression: MIT" in meta)
    ok("Project-URL", meta.count("Project-URL:") >= 3)
    ok("版本一致", f"Version: {__version__}" in meta)


def audit_sdist(sdist: Path) -> None:
    print("[3/6] sdist 内容审计")
    with tarfile.open(sdist) as t:
        names = t.getnames()
    need = ["pyproject.toml", "LICENSE", "README.md"] + [
        f"human_vs_ai/rules/{r}.yaml" for r in RULES] + [
        f"human_vs_ai/{m}.py" for m in ("engine", "cli", "ppl", "report")]
    for n in need:
        ok(n, any(x == n or x.endswith("/" + n) for x in names))


def twine_check() -> None:
    print("[4/6] twine check")
    r = run([sys.executable, "-m", "twine", "check", *[str(p) for p in DIST.iterdir()]],
            cwd=ROOT, timeout=120)
    ok("元数据 PASSED", r.returncode == 0 and "PASSED" in r.stdout,
       (r.stderr or r.stdout)[-300:])
    if r.returncode != 0 and "No module named" in r.stderr:
        print("  twine 未安装：pip install twine")


SMOKE_CMDS = [
    (["profiles"], 0, "academic"),
    (["explain", "L-INFL-01"], 0, "L-INFL-01"),
    (["stats", "-"], 0, '"version"'),
]


def smoke_venv(wheel: Path, sdist: Path) -> None:
    print("[5/6] 干净 venv 装 wheel 冒烟")
    exe = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    with tempfile.TemporaryDirectory(prefix="hva-pkgcheck-") as td:
        vpy = str(Path(td) / "venv" / exe)
        r = run([sys.executable, "-m", "venv", str(Path(td) / "venv")], timeout=120)
        if r.returncode != 0:
            failures.append(f"venv 创建失败: {r.stderr[-200:]}")
            return
        r = run([vpy, "-m", "pip", "install", "--quiet", str(wheel)], timeout=600)
        ok("wheel 安装", r.returncode == 0, r.stderr[-300:])
        if r.returncode != 0:
            return
        cli = [vpy, "-m", "human_vs_ai.cli"]
        for args, code, expect in SMOKE_CMDS:
            r = run([*cli, *args], input=FIXTURE, cwd=ROOT, timeout=120)
            ok(f"{' '.join(args)}", r.returncode == code and expect in r.stdout
               and "Traceback" not in r.stderr, r.stderr[-200:])
        r = run([*cli, "check", "-", "--profile", "academic"], input=FIXTURE,
                cwd=ROOT, timeout=120)
        ok("check 管道", r.returncode == 0 and len(r.stdout) > 200
           and "Traceback" not in r.stderr, r.stderr[-200:])
        r = run([*cli, "ppl", "-"], input=FIXTURE, cwd=ROOT, timeout=120)
        ok("ppl 缺依赖干净退出", r.returncode == 1 and "pip install" in r.stderr
           and "Traceback" not in r.stderr, r.stderr[-200:])
        print("[6/6] 同 venv 改装 sdist（源码分发重建路径）")
        r = run([vpy, "-m", "pip", "install", "--quiet", "--force-reinstall",
                 "--no-deps", str(sdist)], timeout=600)
        ok("sdist 安装", r.returncode == 0, r.stderr[-300:])
        if r.returncode == 0:
            r = run([*cli, "check", "-", "--profile", "official"], input=FIXTURE,
                    cwd=ROOT, timeout=120)
            ok("sdist 装后 check", r.returncode == 0 and "Traceback" not in r.stderr,
               r.stderr[-200:])


def main() -> None:
    wheel, sdist = build()
    audit_wheel(wheel)
    audit_sdist(sdist)
    twine_check()
    smoke_venv(wheel, sdist)
    print()
    if failures:
        sys.exit(f"发布自查未过（{len(failures)} 项）：\n- " + "\n- ".join(failures))
    print("发布自查 PASS：sdist+wheel 内容、元数据、干净环境安装冒烟全部通过。")


if __name__ == "__main__":
    main()
