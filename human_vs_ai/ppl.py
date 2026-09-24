"""句级困惑度（可选依赖：torch + transformers + 一个因果语言模型）。

动机（P3 排队项）：统计层测的是句长节奏/连接词/重复这类表面规律，
语言模型困惑度测的是"这句话对语言模型有多好预测"——AI 句子普遍
偏低（太顺滑）。两个信号互补，且困惑度对改写后"表面规则都过了"
的文本仍有分辨力。

依赖策略：torch/transformers 不进默认依赖（`pip install human-vs-ai[ppl]`），
本模块 import 永不炸——调用 scorer 时才检查依赖并给安装指引。
模型：任意 HF 因果语言模型（`--model` 传路径或 id）。0.6B 级即可出
方向性信号；注意 GGUF（ollama/llama.cpp 权重）不是 HF 格式，加载不了。

口径：
- 每句独立打分：teacher-forced NLL（BOS + 句 token，错一位取对数概率），
  ppl = exp(平均每 token NLL)，avg_nll = 平均每 token 负对数概率（跨句长可比）。
- 超长句按 max_len 滑窗切分，只统计每窗有前文锚点的部分。
- 句子由调用方按 segment.split_document 同口径切出——全项目一个切分层。
"""
from __future__ import annotations

import math

# 依赖缺失时的安装指引（懒检查：模块 import 不触 torch）
DEPS_HINT = "pip install 'human-vs-ai[ppl]'（或 pip install torch transformers）"


def check_deps() -> None:
    """依赖缺失直接抛 ImportError 带指引——静默降级会让人以为打了分。"""
    import importlib
    for mod in ("torch", "transformers"):
        try:
            importlib.import_module(mod)
        except ImportError as e:
            raise ImportError(f"句级困惑度需要 {DEPS_HINT}；缺失：{e}") from e


def nll_to_stats(nlls: list[float]) -> dict | None:
    """一个句子的 token NLL 列表 → {ppl, avg_nll, n_tokens}；无 token 返回 None。

    纯函数，无 torch 依赖——模糊回归与标定脚本都从这里取口径。
    """
    if not nlls:
        return None
    avg = sum(nlls) / len(nlls)
    return {"ppl": math.exp(min(avg, 50.0)), "avg_nll": avg, "n_tokens": len(nlls)}


class SentenceScorer:
    """因果语言模型句级打分器。model 为 HF 路径/id；device 缺省自动选。"""

    def __init__(self, model: str, device: str | None = None, max_len: int = 512):
        check_deps()
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        dtype = torch.float16 if dev == "cuda" else torch.float32
        self._tok = AutoTokenizer.from_pretrained(model)
        self._model = AutoModelForCausalLM.from_pretrained(model, dtype=dtype)
        self._model.to(dev)
        self._dev = dev
        self._model.eval()
        self._max_len = max_len
        self._bos = self._tok.bos_token_id
        if self._bos is None:  # Qwen 系无 bos：用 eos 占位，作为锚不被计分
            self._bos = self._tok.eos_token_id

    def score_sentences(self, sentences: list[str]) -> list[dict | None]:
        """逐句 teacher-forced 打分；空句/全被截掉的句子返回 None。"""
        torch = self._torch
        out: list[dict | None] = []
        with torch.no_grad():
            for sent in sentences:
                ids = self._tok(sent, add_special_tokens=False)["input_ids"]
                if not ids:
                    out.append(None)
                    continue
                nlls: list[float] = []
                # 滑窗：每窗 [锚 token] + 窗内 token 错位取 logprob，只统计窗内部分
                pos = 0
                prev_tail: list[int] = [self._bos] if self._bos is not None else []
                while pos < len(ids):
                    window = ids[pos:pos + self._max_len]
                    toks = prev_tail + window
                    logits = self._model(self._torch.tensor([toks], device=self._dev)).logits[0]
                    logprobs = torch.log_softmax(logits, dim=-1)
                    for j in range(len(prev_tail), len(toks) - 1):
                        nlls.append(-logprobs[j - 1, toks[j]].item())
                    prev_tail = toks[-1:]
                    pos += self._max_len
                out.append(nll_to_stats(nlls))
        return out


def render(sentences: list[str], stats: list[dict | None], model: str) -> str:
    """终端报表：文档序逐句 PPL + 中位数与最顺滑句摘要（居前 10%）。"""
    ok = [(s, st) for s, st in zip(sentences, stats) if st]
    if not ok:
        return "（无可打分的句子）"
    ppls = sorted(st["ppl"] for _, st in ok)
    median = ppls[len(ppls) // 2]
    threshold = ppls[max(0, int(len(ppls) * 0.1))]
    smooth = [(s, st) for s, st in ok if st["ppl"] <= threshold]
    rows = [f"句级困惑度（{model}）", "─" * 46]
    for s, st in zip(sentences, stats):
        if st is None:
            continue
        rows.append(f"{st['ppl']:8.1f}  {s if len(s) <= 50 else s[:47] + '…'}")
    rows.append("─" * 46)
    rows.append(f"共 {len(ok)} 句 · 中位 PPL {median:.1f} · 最顺滑 {len(smooth)} 句（居前 10%）：")
    rows.extend(f"  · {st['ppl']:.1f}  {s if len(s) <= 60 else s[:57] + '…'}" for s, st in smooth[:3])
    return "\n".join(rows)
