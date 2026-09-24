"""文种切片测量与判定（v0.20.0；预声明判定线见 design.md §4）。

数据（gitignore，采集入口见各脚本）：
- AI：gen-oos/gov-genre.jsonl（样本外四模型）+ gen-cal/gov-genre.jsonl（校准多模型）
- 真人：gov-oos/ 湖北/四川/湖南/安徽 四渠道门户公文

切片一律按线上判据 `ood.detect_genre`（as-deployed：报告提示用哪把尺子，
测量就用哪把尺子）。流程对应预声明三分支：
1. 冻结 official 系数测同文种 AUROC；
2. ≥ 0.873（= 基线 0.923 − 0.05，与泛化体检同一容差线）→ 保留全局分；
3. 未达线 → 文种条件逻辑回归分层 5 折交叉验证（真人按渠道、AI 按模型
   分层）——CV-AUROC ≥ 0.873 → 建议落地文种系数；仍不达 → 建议出分抑制。

产出 _qa/genre-check.md 与终端摘要。
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from human_vs_ai import engine, ood, segment  # noqa: E402
from fit_score_tiers import FEATS, auroc, apply_model, fit  # noqa: E402

GEN_OOS = ROOT / "_qa/corpus/gen-oos/gov-genre.jsonl"
GEN_CAL = ROOT / "_qa/corpus/gen-cal/gov-genre.jsonl"
GOV_HU = ROOT / "_qa/corpus/gov-oos"
REPORT = ROOT / "_qa/genre-check.md"

LINE = 0.873          # 预声明判定线 = official auroc_holdout 0.923 − 0.05
BASELINE = 0.923
GENRE_NAME = {"issuance-notice": "印发", "approval-reply": "批复"}


def kinds_of(text: str) -> list[str]:
    sents = [s.text for blk in segment.split_document(text) for s in blk.sents]
    return ood.detect_genre(sents)


def measure(text: str) -> dict | None:
    """冻结全局系数下的分数 + 拟合特征（与 fit_official.extract 同口径）。

    分数不经 analyze 的文种抑制路径——直接 compute_score 挂全局 scoring，
    这样机制落地（印发类 score=None）之后本工具仍能测量文种切片。
    """
    res = engine.analyze(text, "official")
    scoring = engine.load_scoring("official") or {}
    score = engine.compute_score(res.doc_stats, res.findings, res.hints, scoring)
    if score is None:
        return None
    st = res.doc_stats
    n = max(st.n_sentences, 1)
    ungated = sum(engine._SCORE_WEIGHT.get(f.severity, 1.0)
                  for f in res.findings + res.hints if f.para >= 0)
    return {"frozen": score.index,
            "hit_density": ungated / n,
            "sentence_cv": st.sentence_cv,
            "ttr": st.ttr,
            "ngram_repeat": st.ngram_repeat}


def load_rows() -> tuple[list[dict], list[dict]]:
    ai = []
    for src, path in (("oos", GEN_OOS), ("cal", GEN_CAL)):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            ai.append({"model": r["model"], "src": src, "text": r["text"]})
    hu = []
    for f in sorted(GOV_HU.glob("*.json")):
        meta = json.loads(f.read_text(encoding="utf-8"))
        for it in meta["items"]:
            hu.append({"channel": meta["meta"]["channel"], "title": it["title"],
                       "text": it["text"]})
    for r in ai:
        r["feat"] = measure(r["text"])
        r["kinds"] = kinds_of(r["text"]) if r["feat"] else []
    for r in hu:
        r["feat"] = measure(r["text"])
        r["kinds"] = kinds_of(r["text"]) if r["feat"] else []
    return ai, hu


def pct(xs: list[float], p: float) -> int:
    xs = sorted(xs)
    return round(xs[min(int(len(xs) * p), len(xs) - 1)])


def _folds(rows: list[dict], n: int = 5) -> list[list[int]]:
    """按（层，标签）分组轮转分 n 折——每折构成与全集同分布。"""
    rng = random.Random(7)
    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((r["_stratum"], r["_ai"]), []).append(i)
    folds: list[list[int]] = [[] for _ in range(n)]
    for idx in groups.values():
        rng.shuffle(idx)
        for pos, i in enumerate(idx):
            folds[pos % n].append(i)
    return folds


def stratified_cv(ai_k: list[dict], hu_k: list[dict]) -> float:
    """OOF 池化 5 折：按（AI 模型 / 真人渠道）×标签分层轮转分折。

    单一渠道或单一模型独大时，随机分折会"训练集见过同一渠道的姊妹篇"，
    分层轮转保证每折的渠道/模型构成与全集同分布——CV 数字才敢对判定线。
    """
    rows = []
    for r in ai_k:
        rows.append({**r["feat"], "_ai": True, "_stratum": f"ai:{r['model']}"})
    for r in hu_k:
        rows.append({**r["feat"], "_ai": False, "_stratum": f"hu:{r['channel']}"})
    oof: list[tuple[float, bool]] = []
    for f in _folds(rows):
        test_idx = {i for i in f}
        train = [rows[i] for i in range(len(rows)) if i not in test_idx]
        test = [rows[i] for i in sorted(test_idx)]
        coef, intercept = fit(train, FEATS)
        for r in test:
            oof.append((apply_model(r, FEATS, coef, intercept), r["_ai"]))
    return auroc([z for z, ai in oof if ai], [z for z, ai in oof if not ai])


def loco_cv(ai_k: list[dict], hu_k: list[dict], min_test: int = 3) -> float | None:
    """真人渠道留一（LOCO）：留一渠道做测试，其余渠道 + AI 全部做训练。

    分层 CV 的数字可能是"渠道指纹"（模型背下了某渠道的风格而不是学出
    作者信号）——文种系数要服务抓取看不到的新渠道，必须跨渠道外推成立。
    渠道 n < min_test 不做测试折（太抖）；全部不达标时返回 None。
    AI 侧 OOF 用按模型分层的 5 折（真人在训练侧完整出现）。
    """
    channels = [c for c in sorted({r["channel"] for r in hu_k})
                if sum(1 for r in hu_k if r["channel"] == c) >= min_test]
    if len(channels) < 2:
        return None
    oof: list[tuple[float, bool]] = []
    for c in channels:
        train = [{**r["feat"], "_ai": True, "_s": f"ai:{r['model']}"} for r in ai_k]
        train += [{**r["feat"], "_ai": False, "_s": f"hu:{r['channel']}"}
                  for r in hu_k if r["channel"] != c]
        coef, intercept = fit(train, FEATS)
        for r in hu_k:
            if r["channel"] == c:
                oof.append((apply_model(r["feat"], FEATS, coef, intercept), False))
    # AI 侧：按模型分层 5 折（真人全量在训练侧）
    ai_rows = [{**r["feat"], "_ai": True, "_stratum": f"ai:{r['model']}"} for r in ai_k]
    hu_rows = [{**r["feat"], "_ai": False, "_stratum": "hu:all"} for r in hu_k]
    rows = ai_rows + hu_rows
    for f in _folds(rows):
        test_idx = {i for i in f}
        if all(rows[i]["_ai"] is False for i in test_idx):
            continue  # 纯真人折不属于 AI 侧 OOF（渠道留一已覆盖真人侧）
        train = [rows[i] for i in range(len(rows)) if i not in test_idx]
        coef, intercept = fit(train, FEATS)
        for i in sorted(test_idx):
            if rows[i]["_ai"]:
                oof.append((apply_model(rows[i], FEATS, coef, intercept), True))
    return auroc([z for z, ai in oof if ai], [z for z, ai in oof if not ai])


def _breakdown(ai_k: list[dict], hu_k: list[dict]) -> list[str]:
    L = ["", "### 按模型", "", "| 模型 | n | 中位 | ≥80 |", "|---|---|---|---|"]
    by_m: dict[str, list[float]] = {}
    for r in ai_k:
        by_m.setdefault(r["model"], []).append(r["feat"]["frozen"])
    for m, ss in sorted(by_m.items()):
        L.append(f"| {m} | {len(ss)} | {statistics.median(ss):.0f} | "
                 f"{sum(1 for s in ss if s >= 80) / len(ss):.0%} |")
    L += ["", "### 按真人渠道", "", "| 渠道 | n | 中位 | p90 | ≥80 |", "|---|---|---|---|---|"]
    by_c: dict[str, list[float]] = {}
    for r in hu_k:
        by_c.setdefault(r["channel"], []).append(r["feat"]["frozen"])
    for c, ss in sorted(by_c.items()):
        L.append(f"| {c} | {len(ss)} | {statistics.median(ss):.0f} | {pct(ss, 0.9)} | "
                 f"{sum(1 for s in ss if s >= 80) / len(ss):.0%} |")
    L.append("")
    return L


def check_genre(kind: str, ai: list[dict], hu: list[dict]) -> list[str]:
    g = GENRE_NAME[kind]
    ai_k = [r for r in ai if r["feat"] and kind in r["kinds"]]
    hu_k = [r for r in hu if r["feat"] and kind in r["kinds"]]
    head = [f"## {g}（{kind}）", "",
            f"样本：AI {len(ai_k)} · 真人 {len(hu_k)}（判据 `ood.detect_genre` 切片）"]
    if len(ai_k) < 10 or len(hu_k) < 10:
        return head + ["", "样本不足（各 <10），不出判定。", ""]
    ai_s = [r["feat"]["frozen"] for r in ai_k]
    hu_s = [r["feat"]["frozen"] for r in hu_k]
    auc = auroc(ai_s, hu_s)
    L = head + ["",
                f"AI 分数中位 {statistics.median(ai_s):.0f} · "
                f"真人 {statistics.median(hu_s):.0f}（p90 {pct(hu_s, 0.9)}）",
                "",
                f"冻结系数同文种 AUROC：**{auc:.3f}**（判定线 {LINE}）"]
    if auc >= LINE:
        return L + ["", "判定：**达线 → 保留全局分**（文种提示保留）。", ""]
    cv_auc = stratified_cv(ai_k, hu_k)
    loco = loco_cv(ai_k, hu_k)
    # 落地文种系数需两道都过：分层 CV（同分布插值）+ 渠道留一（跨渠道外推）。
    # LOCO 是把验证做严——文种系数要服务抓取看不到的新渠道，渠道指纹过不了这关。
    ship = cv_auc >= LINE and (loco is None or loco >= LINE)
    loco_txt = "（渠道 <2 个，LOCO 不适用）" if loco is None else f"渠道留一 LOCO：**{loco:.3f}**"
    verdict = ("**两道均过 → 建议落地文种系数**（scoring.genre_scoring）"
               if ship else "**未全过 → 建议出分抑制**（score=None + 说明行）")
    return L + ["", f"未达线 → 文种条件逻辑回归分层 5 折 CV：**{cv_auc:.3f}**；{loco_txt}",
                "", f"判定：{verdict}", ""] + _breakdown(ai_k, hu_k)


def main() -> None:
    ai, hu = load_rows()
    ai_ok = sum(1 for r in ai if r["feat"])
    hu_ok = sum(1 for r in hu if r["feat"])
    print(f"AI {ai_ok}/{len(ai)} 过门槛 · 真人 {hu_ok}/{len(hu)} 过门槛")
    L = ["# 文种切片测量与判定（v0.20.0）", "",
         f"判定线 {LINE}（= official 基线 {BASELINE} − 0.05，预声明于 design.md §4）。",
         "切片按线上判据 `ood.detect_genre`；<8 句不入样。",
         f"语料：AI gen-oos + gen-cal（{ai_ok} 篇过门槛）· 真人四省门户（{hu_ok} 篇过门槛）。",
         ""]
    for kind in ("issuance-notice", "approval-reply"):
        L += check_genre(kind, ai, hu)
    # 对照：判据 vs 标题切片的一致面（只报数量，不做依据）
    L += ["## 判据 × 标题对照", "",
          "| 文种 | 判据切片 | 其中标题同文种 | 标题切片但判据未中 |", "|---|---|---|---|"]
    hu_feat = [r for r in hu if r["feat"]]
    title_re = {"issuance-notice": "印发", "approval-reply": "批复"}
    for kind, word in title_re.items():
        by_det = [r for r in hu_feat if kind in r["kinds"]]
        both = [r for r in by_det if word in r["title"]]
        by_title = [r for r in hu_feat if word in r["title"]]
        miss = [r for r in by_title if kind not in r["kinds"]]
        L.append(f"| {GENRE_NAME[kind]} | {len(by_det)} | {len(both)} | {len(miss)} |")
    L.append("")
    REPORT.write_text("\n".join(L), encoding="utf-8")
    print(f"报告：{REPORT}")


if __name__ == "__main__":
    main()
