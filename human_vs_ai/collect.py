"""校准样本导出：把一次真实分析变成可贡献的匿名校准样本（自愿提交）。

脱敏先于导出：手机号/邮箱/身份证/长数字串打码（这些不影响 AI 味
统计，但属于个人身份信息）；文本正文原样保留——AI 味特征就在原文里，
脱敏过度样本就没用了。样本只含文本与判定结果，不含任何文件路径。
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import __version__, engine

# 个人身份信息打码（顺序即执行顺序：身份证先于长数字，避免被通配吃掉）
_SENSITIVE = [
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "【身份证号】"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "【手机号】"),
    (re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9._-]{1,63})+"), "【邮箱】"),
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "【卡号】"),
]


def sanitize(text: str) -> str:
    for rx, placeholder in _SENSITIVE:
        text = rx.sub(placeholder, text)
    return text


LABELS = {"miss": "漏报（AI 味没报出来）", "fp": "误报（人写被误伤）", "hit": "判定准确"}


def build_sample(text: str, profile: str, label: str) -> dict:
    """一次分析 → 一条校准样本（JSONL 行）。"""
    result = engine.analyze(text, profile)
    clean = sanitize(text)
    return {
        "type": "human-vs-ai-calibration",
        "version": __version__,
        "time": time.strftime("%Y-%m"),
        "label": label,  # miss=漏报 / fp=误报 / hit=判定准确
        "profile": profile,
        "text": clean,
        "text_sha256": hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16],
        "score": round(result.score.index) if result.score else None,
        "findings": sorted({f.rule_id for f in result.findings}),
        "n_chars": result.doc_stats.n_chars,
    }


def render_jsonl(samples: list[dict]) -> str:
    return "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n"
