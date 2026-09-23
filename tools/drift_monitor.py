"""漂移监测：collect 样本按月聚合，检测词表/评分的分布漂移。

为什么需要：模型在换代，文风在漂移——静态词表的"准确"是某一刻的
快照。collect 匿名样本入口持续进料后，本脚本把校准从一次性动作变成
可重复的机制：分数分布或规则命中率的显著变化 = 漂移信号，触发词表
复审（design.md §4 的当代语料校准闭环）。

漂移判据（可解释，全部写进报告）：
- 某场景指数 p50 变化 ≥ 15 分（p90 ≥ 15 同判）
- 某规则命中率变化 ≥ 10 个百分点
- 出分率（≥8 句可判文本占比）变化 ≥ 20 个百分点

用法：
  python tools/drift_monitor.py --input corpus_private/*.jsonl            # 对比基线
  python tools/drift_monitor.py --input a.jsonl --update-baseline        # 存为新基线

基线 _qa/drift-baseline.json 只含统计分位与命中率（无任何文本），
可入库；样本本体 corpus_private/ 在 .gitignore。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
BASELINE = ROOT / "_qa" / "drift-baseline.json"

P50_DRIFT = 15    # 指数分位漂移阈值（分）
RULE_DRIFT = 0.10  # 规则命中率漂移阈值（比例）
SCORE_RATE_DRIFT = 0.20  # 出分率漂移阈值（比例）


def percentile(xs: list[int], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(int(len(s) * p), len(s) - 1)]


def load_samples(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                print(f"跳过 {p.name} 中一条坏行", file=sys.stderr)
                continue
            if r.get("type") != "human-vs-ai-calibration":
                continue
            rows.append(r)
    return rows


def aggregate(rows: list[dict], by_month: bool = False) -> dict:
    """聚合样本：对比用 profile 级（漂移是跨月的，按月分组就测不到了——
    测试实证），展示用 (profile, month) 级。

    输出：分数分位、出分率、规则命中率。
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        key = (r.get("profile", "?"), r.get("time", "?") if by_month else "")
        groups[key].append(r)
    out = {}
    for (profile, _month), rs in sorted(groups.items()):
        scores = [r["score"] for r in rs if r.get("score") is not None]
        rule_hit: dict[str, int] = defaultdict(int)
        for r in rs:
            for rid in r.get("findings") or []:
                rule_hit[rid] += 1
        out[f"{profile}|{_month}" if _month else profile] = {
            "n": len(rs),
            "score_rate": len(scores) / len(rs) if rs else 0.0,
            "p50": percentile(scores, 0.5),
            "p90": percentile(scores, 0.9),
            "rules": {k: v / len(rs) for k, v in rule_hit.items()},
        }
    return out


def compare(old: dict, new: dict) -> list[str]:
    """同 key 场景漂移信号；只在新旧都存在的组间对比。"""
    signals = []

    def finite(x) -> bool:
        return x is not None and x == x  # None / NaN（无样本组分位）都不比

    for key, cur in new.items():
        base = old.get(key)
        if not base:
            continue
        for stat, name in (("p50", "指数 p50"), ("p90", "指数 p90")):
            b, c = base.get(stat), cur.get(stat)
            if finite(b) and finite(c) and abs(c - b) >= P50_DRIFT:
                signals.append(f"[{key}] {name}漂移 {b:.0f} → {c:.0f}（≥{P50_DRIFT} 分）")
        b_rate, c_rate = base.get("score_rate", 0), cur.get("score_rate", 0)
        if abs(c_rate - b_rate) >= SCORE_RATE_DRIFT:
            signals.append(f"[{key}] 出分率漂移 {b_rate:.0%} → {c_rate:.0%}")
        for rid, rate in cur.get("rules", {}).items():
            b_rate = base.get("rules", {}).get(rid, 0.0)
            if abs(rate - b_rate) >= RULE_DRIFT:
                signals.append(
                    f"[{key}] 规则 {rid} 命中率 {b_rate:.0%} → {rate:.0%}（≥{RULE_DRIFT:.0%}）")
    return signals


def render(groups: dict) -> str:
    lines = ["# collect 样本分布", ""]
    for key, g in groups.items():
        rules = "、".join(f"{k} {v:.0%}" for k, v in
                          sorted(g["rules"].items(), key=lambda x: -x[1])[:5]) or "—"
        lines.append(
            f"- **{key}**：{g['n']} 篇 · 出分率 {g['score_rate']:.0%} · "
            f"指数 p50 {g['p50']:.0f} / p90 {g['p90']:.0f} · top 命中 {rules}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", nargs="+", required=True, help="collect JSONL（支持 glob）")
    ap.add_argument("--update-baseline", action="store_true",
                    help="把本次聚合存为新基线（只含统计，不含文本）")
    args = ap.parse_args()

    paths: list[Path] = []
    for pat in args.input:
        p = Path(pat)
        if p.is_absolute():
            # Windows 绝对路径不能进 glob（Non-relative patterns）；
            # 通配需求用 glob 模块处理
            import glob as _glob
            paths.extend(sorted(Path(x) for x in _glob.glob(pat)) or [p])
        elif any(ch in pat for ch in "*?["):
            paths.extend(sorted(Path().glob(pat)))
        else:
            paths.append(p)
    paths = [p for p in paths if p.exists()]
    if not paths:
        sys.exit(f"没有找到输入文件：{args.input}")

    rows = load_samples(paths)
    if not rows:
        sys.exit("输入里没有 human-vs-ai-calibration 样本")
    print(render(aggregate(rows, by_month=True)))

    groups = aggregate(rows)  # profile 级：对比与基线用
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    if args.update_baseline:
        BASELINE.write_text(json.dumps(groups, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"\n基线已写入 {BASELINE}")
        return
    if baseline is None:
        print("\n无基线。确认当前分布合理后运行："
              "python tools/drift_monitor.py --input ... --update-baseline")
        return

    signals = compare(baseline, groups)
    print("\n# 漂移信号")
    if signals:
        print("\n".join(f"- ⚠ {s}" for s in signals))
        print("\n建议：核对漂移场景的词表与阈值（docs/rules.md §9 流程）。")
    else:
        print("- 无显著漂移（阈值：p50 ≥15 分 / 规则 ≥10pp / 出分率 ≥20pp）")


if __name__ == "__main__":
    main()
