"""私人口味语料提取：从 persona-stream 事件流产出 corpus_private/（永不入库）。

边界（硬性）：源目录只读，本脚本对它只做 open(..., "r")；全部产出落在
D:/tools/human-vs-ai/corpus_private/，该目录在 .gitignore 里。开源文档只允许
出现合成样例。

产出：
  human_base.txt / human_base.jsonl   全部用户输入（真人基线语料）
  human_base_stats.json               基线统计（含与先验的对账）
  seed_prompts.jsonl                  四条手工确认的种子 prompt 全文
  taste_pairs.jsonl                   文案池标注链：被毙稿 / 定稿 / 我亲改
  raw_pairs.jsonl                     关键词扩池：命中 prompt + 5 分钟内 AI 产出
  ai_prose.jsonl                      AI 产线中文散文（风格指纹对照组）
  taste_stats.json                    口味规则的量化依据（H5 指纹 + 池判别特征）

用法：python tools/extract_private_corpus.py [--data D:/persona-stream/data]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "corpus_private"
DEFAULT_DATA = Path("D:/persona-stream/data")

# 种子：eggpaper 文案池「AI 稿 → 我毙/我亲改 → 定稿」标注链
SEED_IDS = ["baa3b79659ea", "9b5d13308d43", "b7ff52af1721", "141fc036e00c"]
# 两轮成批毙稿的 prompt（正文里逐条列着被毙的 AI 稿）
VETO_PROMPTS = {
    "2bba79e82622": "round1_暖心稿",
    "baa3b79659ea": "round2_抒情稿",
}
SEED_FILE = "events-20260919.jsonl"
# 定稿池的当前状态（eggpaper 仓库，只读；缺失时跳过该项）
FINAL_POOL = Path("D:/tools/eggpaper/frontend/src/App.vue")
POOL_RE = re.compile(r"GREET_POOL\s*=\s*\{(.*?)\n\}", re.S)

# 扩池关键词：文风类指令标记
KEYWORDS = ["改成：", "（改成", "删除文案", "不要有无用文案", "AI味",
            "啰嗦", "生硬", "贴近手写", "不要那么重"]

# 先验（persona 档案记录）：用于对账，偏差超阈值要查解析
PRIOR_MEDIAN_CHARS = 188
PRIOR_MIXED_RATIO = 0.737
PRIOR_N = 3591
PRIOR_CUT = "2026-09-08"  # 先验快照的语料截断点（累积计数反推）

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
# 中英混用：含英文词（≥2 字母）——路径/标识符里的英文也算（与先验口径一致：
# 该口径在 09-07 快照上复现 76.2%，贴近先验 73.7%）
_EN_WORD = re.compile(r"[A-Za-z]{2,}")
# 改写标注：「（改成：X）」「（我提供一个：X）」「N 条改成：X」「改成：X」
_PAIR_MARKS = re.compile(r"[（(](?:改成|我提供一个)[:：]\s*([^）)]+)[）)]")
_INLINE_PAIR = re.compile(r"^(.*?)[。.]?\s*(?:这[两三四五六七八九]+[条个]?|这个|这两个)?\s*改成[:：]\s*(.+)$", re.S)
# 用户 prompt 里以 "/" 起始的候选项行
_SLASH_ITEM = re.compile(r"(?:^|[\s。；;])\s*/\s*([^\n]+)")
# 成批毙稿的收尾判决语（要剥离，剩下的才是被毙清单）
_VERDICT = re.compile(r"[，,。]?\s*(你的组合很奇怪|这些有点怪|这些有点|这个组合)[^\n]*$")
# 自动化任务模板特征词（统计时要剔噪，不是"个人表达"）
AUTO_MARKS = ("persona-stream", "polyforge")


def load_events(data_dir: Path):
    """流式读全部事件（只读）。返回 {session: [event...]} 与钩子计数。"""
    files = sorted(glob.glob(str(data_dir / "events-*.jsonl")))
    if not files:
        sys.exit(f"找不到语料：{data_dir}/events-*.jsonl")
    by_session: dict[str, list[dict]] = collections.defaultdict(list)
    hooks: collections.Counter = collections.Counter()
    n = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:  # 只读，不写源目录
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n += 1
                hooks[e.get("hook", "?")] += 1
                by_session[str(e.get("session", ""))].append(e)
    for evs in by_session.values():
        evs.sort(key=lambda e: e.get("ts", ""))
    return by_session, hooks, n, len(files)


def _ts(e: dict):
    try:
        return datetime.fromisoformat(e["ts"])
    except Exception:
        return None


def _tool_text(payload: dict) -> str:
    """从 PostToolUse 的 tool_input 里抽"写入的文本"。"""
    ti = payload.get("tool_input") or {}
    tool = payload.get("tool_name") or ""
    if tool == "Write":
        return ti.get("content", "") or ""
    if tool in ("Edit", "MultiEdit"):
        return ti.get("new_string", "") or ""
    if tool == "NotebookEdit":
        return ti.get("new_source", "") or ""
    return ""


def _is_mixed(text: str) -> bool:
    """含英文词（先验用的就是这个口径——该口径在 09-07 快照上复现 76.2%，
    贴近先验 73.7%；要求"中日英同时出现"会掉到 53%，那不是先验的算法）。"""
    return bool(_EN_WORD.search(text))


def _is_bilingual(text: str) -> bool:
    """中英同时出现（更严格的语文学口径，报告里并列展示供交叉核对）。"""
    return bool(_CJK.search(text)) and bool(_EN_WORD.search(text))


def base_stats(prompts: list[dict]) -> dict:
    chars = [len(p["text"]) for p in prompts]
    mixed = sum(1 for p in prompts if _is_mixed(p["text"]))
    bil = sum(1 for p in prompts if _is_bilingual(p["text"]))
    joined = "\n".join(p["text"] for p in prompts)
    return {
        "n_prompts": len(prompts),
        "median_chars": statistics.median(chars),
        "mean_chars": round(statistics.mean(chars), 1),
        "p25_chars": statistics.quantiles(chars, n=4)[0],
        "p75_chars": statistics.quantiles(chars, n=4)[2],
        "max_chars": max(chars),
        "mixed_ratio": round(mixed / len(prompts), 4),
        "bilingual_ratio": round(bil / len(prompts), 4),
        "cjk_ratio": round(sum(1 for p in prompts if _CJK.search(p["text"])) / len(prompts), 4),
        "total_chars": len(joined),
        # 标点/风格指纹（每千字，口味规则 H5 用）
        "colon_per_kchar": round(joined.count("：") / len(joined) * 1000, 3),
        "dash_per_kchar": round(joined.count("—") / len(joined) * 1000, 3),
        "ellipsis_per_kchar": round(joined.count("…") / len(joined) * 1000, 3),
        "excl_per_kchar": round((joined.count("！") + joined.count("!")) / len(joined) * 1000, 3),
        "question_per_kchar": round((joined.count("？") + joined.count("?")) / len(joined) * 1000, 3),
    }


def prior_reconciliation(prompts: list[dict]) -> dict:
    """与 persona 先验对账：先验是 2026-09-07/08 的语料快照口径。

    语料在长（今日 4536 条 vs 快照期约 3580 条），全量中位数被后加进来的
    短指令与自动化模板拉低——对账要在同一截断口径上比，不能拿全量去比快照。
    """
    cut = [p for p in prompts if (p["ts"] or "") < PRIOR_CUT]
    st = base_stats(cut)
    dev = abs(st["median_chars"] - PRIOR_MEDIAN_CHARS) / PRIOR_MEDIAN_CHARS
    return {
        "prior": {"n": PRIOR_N, "median_chars": PRIOR_MEDIAN_CHARS,
                  "mixed_ratio": PRIOR_MIXED_RATIO, "snapshot_cut": PRIOR_CUT},
        "snapshot": {"n": st["n_prompts"], "median_chars": st["median_chars"],
                     "mixed_ratio": st["mixed_ratio"]},
        "median_deviation": round(dev, 4),
        "verdict": "PASS" if dev < 0.15 else "FAIL",
        "note": "全量中位低于先验是语料增长的正常结果（新增多为短指令），非解析错误",
    }


def parse_seed_pairs(prompts: list[dict]) -> list[dict]:
    """把种子 prompt 里的改写标注拆成结构化对。

    两种形态：
    - 显式标注：「（改成：X）」贴着 AI 原句同行
    - 成批毙稿：「…/ 句A / 句B …（判决语）」——斜杠列表即被毙清单
    """
    pairs: list[dict] = []
    for p in prompts:
        text = p["text"]
        for m in _PAIR_MARKS.finditer(text):
            mine = m.group(1).strip()
            head = text[max(0, m.start() - 120):m.start()]
            cand = re.split(r"[/\n]", head)
            ai_line = cand[-1].strip(" 　。") if cand else ""
            ai_line = re.sub(r"^[（(]", "", ai_line).strip()
            if ai_line and mine:
                pairs.append({
                    "source_id": p["id"], "kind": "explicit_mark",
                    "ai_text": ai_line, "my_text": mine, "reason": "",
                })
        m = _INLINE_PAIR.match(text.strip())
        if m and "改成" in text:
            head, tail = m.group(1), m.group(2)
            ai_lines = [s.strip(" 　/") for s in _SLASH_ITEM.findall(head)]
            my_lines = [s.strip(" 　/") for s in _SLASH_ITEM.findall(tail)]
            ai_lines = [s for s in ai_lines if s]
            my_lines = [s for s in my_lines if s]
            if ai_lines and my_lines:
                pairs.append({
                    "source_id": p["id"], "kind": "bulk_rewrite",
                    "ai_text": " / ".join(ai_lines), "my_text": " / ".join(my_lines),
                    "reason": "",
                })
    return pairs


def extract_veto_lists(seed_events: list[dict]) -> list[dict]:
    """从成批毙稿的 prompt 正文里切出被毙清单。

    正文形态是一行斜杠列表 + 收尾判决语。按 "/" 切（不能用正则 findall——
    捕获到行尾会把后续条目一起吞掉），再剥掉判决语与夹注。
    """
    out = []
    for e in seed_events:
        round_name = VETO_PROMPTS.get(e.get("id"))
        if not round_name:
            continue
        body = _VERDICT.sub("", e["text"].strip())
        items: list[str] = []
        for raw in body.split("/"):
            s = re.sub(r"^[\s。；;]+", "", raw).strip()
            s = re.sub(r"[（(](?:改成|我提供一个)[:：].*?[）)]", "", s).strip()
            # 判决语残片与开场白不是文案
            if not s or len(s) < 4:
                continue
            if re.search(r"(换一批|多查一些|神似即可|不用形似|这个点的?你，?$)", s):
                continue
            items.append(s)
        out.append({"round": round_name, "source_id": e["id"], "ts": e["ts"],
                    "verdict": e["text"].strip()[-40:], "lines": items})
    return out


def extract_final_pool(path: Path) -> list[str]:
    """读 eggpaper 当前定稿池（只读；仓库不在则返回空）。"""
    if not path.exists():
        return []
    m = POOL_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        return []
    return [s for s in re.findall(r"'([^']+)'", m.group(1)) if s.strip()]


# 度量词表（具体名词 / 网络梗）来自 corpus_private/dims_vocab.json——
# 语料派生词汇属于私库数据，不进开源代码；缺失时退到通用网络词兜底。
_VOCAB_PATH = OUT / "dims_vocab.json"


def _vocab(key: str, fallback: str) -> str:
    try:
        words = json.loads(_VOCAB_PATH.read_text(encoding="utf-8")).get(key) or []
    except Exception:
        words = []
    return "|".join(re.escape(w) for w in words) if words else fallback


def _meme_pattern() -> str:
    return rf"({_vocab('meme', '牛马|摸鱼|摆烂|连滚带爬|火葬场|狠人')})"


def _noun_pattern() -> str:
    return rf"({_vocab('noun', 'DDL|组会|文献|论文|数据|导师')})"


# 池判别特征（被毙 vs 定稿）：口味规则 T1–T4 的量化依据
POOL_FEATURES = {
    "劝慰句": r"[，,].{0,14}(跑不了|还在原地|不丢人|不算熬夜|是正常|别硬撑|又不会跑|不会跑)",
    "抒情升华": r"[，,](就是|才是|是)(最好的|你的|一场|偷来的)|陪你|就是你的",
    "说理解释": r"(不是.{2,8}(问题|错|摆烂|态度|罪)|是科学|是生理学|不心虚)",
    "效率承诺": r"(效率|翻倍|事半功倍)",
    "关怀祈使": r"(早点睡|别熬|照顾好|注意身体|保重)",
    "夸张赞美": r"(太阳还没|比月亮|最努力|最棒)",
    "「X的你」句式": r"的?你[，,].{0,10}[，,]|^[^，]{0,6}的你[，,]",
    "空泛鼓励": r"(好好读|加油|稳住|冲鸭|你可以的)",
    "具体名词": _noun_pattern(),
    "网络梗/口语": _meme_pattern(),
}


def pool_feature_stats(vetoed: list[str], kept: list[str]) -> dict:
    def rate(lines, pat):
        return round(sum(1 for s in lines if re.search(pat, s)) / len(lines), 3) if lines else 0.0
    return {
        "n_vetoed": len(vetoed), "n_kept": len(kept),
        "vetoed_mean_chars": round(statistics.mean(len(s) for s in vetoed), 1) if vetoed else 0,
        "kept_mean_chars": round(statistics.mean(len(s) for s in kept), 1) if kept else 0,
        "features": {
            name: {"vetoed": rate(vetoed, pat), "kept": rate(kept, pat)}
            for name, pat in POOL_FEATURES.items()
        },
    }


AI_PROSE_MARKS = {
    "colon": r"[:：]", "dash": r"—", "not_but": r"(不是|并非).{2,20}(而是|，是)",
    "triad": r"[\u4e00-\u9fff]{2,8}、[\u4e00-\u9fff]{2,8}、[\u4e00-\u9fff]{2,8}",
    "copula_hype": r"(就是|便是)(最好的|最|一种|你的|属于)",
    "summarizer": r"(综上所述|总而言之|总之|总的来说|由此可见)",
    "excl": r"[！!]", "question": r"[？?]",
}

# 手工确认的改写参考对放在 corpus_private/curated_rewrites.json（不入库）：
# 每条含 id / ai / mine / why，来源为种子 prompt 里我亲口给出的改写。
# 本脚本只负责把它和标注链一起打包成 taste_reference.json。
CURATED_PATH = OUT / "curated_rewrites.json"
DIMS_VOCAB = OUT / "dims_vocab.json"


def load_curated() -> list[dict]:
    if not CURATED_PATH.exists():
        print(f"提示：{CURATED_PATH} 不存在，改写参考对跳过（该文件是私库数据，不入库）")
        return []
    return json.loads(CURATED_PATH.read_text(encoding="utf-8"))


def build_reference(vetoed: list[str], kept: list[str], rewrites: list[dict]) -> dict:
    """回归参考集：被毙稿 / 定稿 / 亲改对（改写维度判定用）。"""
    def dims(from_text: str, to_text: str) -> dict:
        """改写是否命中风格维度：更短 / 含梗或具体名词 / 无说明腔（三中其二）。"""
        shorter = len(to_text) < len(from_text)
        cjk_nouns = re.compile(r"(DDL|deadline|组会|参考文献|文献|论文|科研|数据|导师|paper|accept)")
        meme = re.compile(_meme_pattern())
        has_meme_or_noun = bool(meme.search(to_text) or cjk_nouns.search(to_text))
        no_manual = not re.search(r"(自动挑|先侦查|点击|拖[入拽]|勾选|一次最多|请直接|支持|可[以选])", to_text)
        return {"更短": shorter, "含梗或具体名词": has_meme_or_noun, "无说明腔": no_manual,
                "hit": sum([shorter, has_meme_or_noun, no_manual]) >= 2}

    return {
        "vetoed": vetoed,
        "kept": kept,
        "rewrites": [{**r, "dims": dims(r["ai"], r["mine"])} for r in rewrites],
        "n_rewrites": len(rewrites),
    }


def _rate_per_kchar(lines: list[str], pat: str) -> float:
    joined = "\n".join(lines)
    return round(len(re.findall(pat, joined)) / len(joined) * 1000, 3) if joined else 0.0


def extract_ai_prose(by_session, out_path: Path) -> list[str]:
    """AI 产线中文散文：Write/Edit 写入文本里 CJK 占比 ≥60% 的行——风格指纹对照。"""
    blocks: list[str] = []
    with out_path.open("w", encoding="utf-8") as fh:
        for evs in by_session.values():
            for e in evs:
                if e.get("hook") != "PostToolUse":
                    continue
                ti = (e.get("payload") or {}).get("tool_input") or {}
                tool = (e.get("payload") or {}).get("tool_name")
                txt = ""
                if tool == "Write":
                    txt = ti.get("content", "") or ""
                elif tool in ("Edit", "MultiEdit"):
                    txt = ti.get("new_string", "") or ""
                if len(txt) < 40:
                    continue
                for ln in txt.split("\n"):
                    ln = ln.strip()
                    if len(ln) < 20:
                        continue
                    nonspace = re.sub(r"\s", "", ln)
                    if not nonspace or len(_CJK.findall(ln)) / len(nonspace) < 0.6:
                        continue
                    blocks.append(ln)
                    fh.write(json.dumps({"event_id": e.get("id"), "ts": e.get("ts"),
                                         "file": ti.get("file_path", ""), "text": ln},
                                        ensure_ascii=False) + "\n")
    return blocks


def collect_ai_outputs(events: list[dict], ts, window_min: int = 5) -> list[dict]:
    """取某时刻之后 window 分钟内、同会话的 AI 写入产出。"""
    if ts is None:
        return []
    end = ts + timedelta(minutes=window_min)
    out = []
    for e in events:
        if e.get("hook") != "PostToolUse":
            continue
        et = _ts(e)
        if et is None or not (ts <= et <= end):
            continue
        text = _tool_text(e.get("payload") or {})
        if not text.strip():
            continue
        out.append({
            "ts": e.get("ts"), "event_id": e.get("id"),
            "tool": (e.get("payload") or {}).get("tool_name"),
            "file": ((e.get("payload") or {}).get("tool_input") or {}).get("file_path", ""),
            "text": text,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--final-pool", default=str(FINAL_POOL))
    args = ap.parse_args()
    data_dir = Path(args.data)

    by_session, hooks, n_events, n_files = load_events(data_dir)
    prompts = [e for evs in by_session.values() for e in evs if e.get("hook") == "UserPromptSubmit"]
    prompts.sort(key=lambda e: e.get("ts", ""))
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- 1. 真人基线 ----
    (OUT / "human_base.txt").write_text(
        "\n\n".join(p.get("text", "") for p in prompts), encoding="utf-8"
    )
    with (OUT / "human_base.jsonl").open("w", encoding="utf-8") as fh:
        for p in prompts:
            fh.write(json.dumps({"id": p.get("id"), "ts": p.get("ts"),
                                 "session": p.get("session"), "chars": p.get("chars"),
                                 "text": p.get("text", "")}, ensure_ascii=False) + "\n")
    st = base_stats(prompts)
    recon = prior_reconciliation(prompts)

    # ---- 2. 种子 prompt + 标注链 ----
    seed_events = []
    seed_path = data_dir / SEED_FILE
    if seed_path.exists():
        with seed_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if e.get("id") in SEED_IDS or e.get("id") in VETO_PROMPTS:
                    seed_events.append(e)
    seed_events.sort(key=lambda e: e.get("ts", ""))
    with (OUT / "seed_prompts.jsonl").open("w", encoding="utf-8") as fh:
        for e in seed_events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    veto = extract_veto_lists(seed_events)
    vetoed_lines = [s for r in veto for s in r["lines"]]
    kept_lines = extract_final_pool(Path(args.final_pool))
    pairs = parse_seed_pairs(seed_events)
    with (OUT / "taste_pairs.jsonl").open("w", encoding="utf-8") as fh:
        for r in veto:
            for s in r["lines"]:
                fh.write(json.dumps({"kind": "vetoed", "round": r["round"],
                                     "source_id": r["source_id"], "ai_text": s,
                                     "my_text": "", "reason": r["verdict"]},
                                    ensure_ascii=False) + "\n")
        for s in kept_lines:
            fh.write(json.dumps({"kind": "kept", "round": "final_pool",
                                 "source_id": "eggpaper/App.vue", "ai_text": "",
                                 "my_text": s, "reason": "定稿存活"},
                                ensure_ascii=False) + "\n")
        for p in pairs:
            fh.write(json.dumps({**p, "kind": "my_rewrite"}, ensure_ascii=False) + "\n")

    # ---- 3. 关键词扩池 ----
    n_kw = 0
    with (OUT / "raw_pairs.jsonl").open("w", encoding="utf-8") as fh:
        for p in prompts:
            text = p.get("text", "")
            hit = [k for k in KEYWORDS if k in text]
            if not hit:
                continue
            n_kw += 1
            outs = collect_ai_outputs(by_session.get(str(p.get("session")), []), _ts(p))
            fh.write(json.dumps({
                "prompt_id": p.get("id"), "ts": p.get("ts"), "session": p.get("session"),
                "cwd": p.get("cwd"), "keywords": hit, "chars": p.get("chars"),
                "prompt": text, "ai_outputs": outs,
            }, ensure_ascii=False) + "\n")

    # ---- 4. AI 产线散文 + 风格指纹 ----
    ai_prose = extract_ai_prose(by_session, OUT / "ai_prose.jsonl")
    personal = [p["text"] for p in prompts
                if not any(a in p["text"] or a in p["text"].lower() for a in AUTO_MARKS)]
    style = {
        "human": {"n_lines": len(personal), "n_chars": sum(len(s) for s in personal),
                  **{k: _rate_per_kchar(personal, v) for k, v in AI_PROSE_MARKS.items()}},
        "ai_prose": {"n_lines": len(ai_prose), "n_chars": sum(len(s) for s in ai_prose),
                     **{k: _rate_per_kchar(ai_prose, v) for k, v in AI_PROSE_MARKS.items()}},
    }
    taste_stats = {
        "human_base": st,
        "prior_reconciliation": recon,
        "style_fingerprint_per_kchar": style,
        "pool_features": pool_feature_stats(vetoed_lines, kept_lines),
        "veto_rounds": [{"round": r["round"], "source_id": r["source_id"],
                         "n_lines": len(r["lines"])} for r in veto],
        "n_my_rewrites": len(pairs),
    }
    (OUT / "human_base_stats.json").write_text(
        json.dumps({"human_base": st, "prior_reconciliation": recon}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (OUT / "taste_stats.json").write_text(
        json.dumps(taste_stats, ensure_ascii=False, indent=1), encoding="utf-8")
    ref = build_reference(vetoed_lines, kept_lines, load_curated())
    if DIMS_VOCAB.exists():
        ref["dims_vocab"] = json.loads(DIMS_VOCAB.read_text(encoding="utf-8"))
    (OUT / "taste_reference.json").write_text(
        json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"事件 {n_events} 条 / {n_files} 文件 · hooks: {dict(hooks.most_common(4))}")
    print(f"用户输入 {len(prompts)} 条 → human_base.txt")
    print(f"  全量：中位 {st['median_chars']:.0f} 字 · 中英混用 {st['mixed_ratio']:.1%}")
    print(f"  先验对账（截断 {PRIOR_CUT}）：n={recon['snapshot']['n']} "
          f"中位 {recon['snapshot']['median_chars']:.0f}（先验 {PRIOR_MEDIAN_CHARS}，"
          f"偏差 {recon['median_deviation']:.1%}）· 混用 {recon['snapshot']['mixed_ratio']:.1%}"
          f"（先验 {PRIOR_MIXED_RATIO:.1%}） → {recon['verdict']}")
    print(f"标注链：被毙 {len(vetoed_lines)} 条（{len(veto)} 轮）· 定稿 {len(kept_lines)} 条"
          f" · 我亲改 {ref['n_rewrites']} 组 → taste_pairs.jsonl")
    print(f"关键词扩池命中 {n_kw} 条 → raw_pairs.jsonl；AI 产线散文 {len(ai_prose)} 行 → ai_prose.jsonl")
    pf = taste_stats["pool_features"]["features"]
    print("池判别特征（被毙 vs 定稿）：" + " · ".join(
        f"{k} {v['vetoed']:.0%}/{v['kept']:.0%}" for k, v in pf.items()))
    print(f"产出目录：{OUT}")


if __name__ == "__main__":
    main()
