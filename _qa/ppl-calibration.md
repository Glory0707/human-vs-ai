# 句级困惑度标定（P3 句级困惑度，Qwen3-0.6B 冻结零训练）

模型：_qa/models/qwen3-0.6b-base（HF 格式，bf16；PPL 口径见 human_vs_ai/ppl.py）。
语料：AI gen-oos 四模型 vs 真人豆瓣/果壳（样本外问答集，`--sample 25`）。
方向 = AI 更顺滑（PPL 更低），AUROC < 0.5 时区分度取 1-AUROC。

| doc 级特征 | AUROC | 区分度（AI 顺滑方向） |
|---|---|---|
| median_ppl | 0.483 | **0.517** |
| mean_ppl | 0.367 | **0.633** |
| p10_ppl | 0.447 | **0.553** |

结论：信号弱（最强 doc 特征区分度 0.633），作为统计层之外的互补视角入可选依赖。

AI median_ppl 中位 176.6 · 真人 212.7
