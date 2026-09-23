# official 场景当代语料扩充与验证

日期：2026-09。回答的问题：official 词表在**当代模型生成的公文**上
是否有效？真人公文是否被误伤？

## 语料

| 语料 | 构成 | 说明 |
|---|---|---|
| gen2026/official.jsonl | 2026 当季 9 模型 × 16 个公文题目，70 篇 | tools/gen_samples.py `--scene official`（通知/通报/请示/纪要/实施方案等） |
| _qa/corpus/gov/ | 真人公文 15 篇（gov.cn 规章文件） | 对照组 |

## 结果

**词表方向（AI 命中率 − 真人命中率）**：

| 规则 | AI | 真人 | 方向 |
|---|---|---|---|
| O-TAIL-01 | 4.3% | 0% | AI↑ |
| O-EXCL-01 | 2.9% | 0% | AI↑ |
| D-STKD-01 | 2.9% | 0% | AI↑ |
| D-DASH-01 | 2.9% | 0% | AI↑ |
| O-INFL-01 | 1.4% | 0% | AI↑ |

**组合覆盖**：9/9 模型的 AI 公文 100% 至少命中一条规则；真人公文 0 命中。

**句长 CV**：AI 公文中位 0.75–0.92，真人 0.879——公文文体条款式短句
天然高 CV，节奏类指标（D-UNIF）在本场景不构成信号，词表组合是主力。

## 结论与边界

1. **词表组合有效**：单条规则命中率低（公文模板化程度天然高，词表
   只能抓增量痕迹），但组合覆盖 9 模型全捕获、真人零误伤——方向正确。
2. **证据等级：初步**。真人侧仅 15 篇且为规章文体（与 AI 生成的
   600 字事务公文不同文种）；评分拟合继续排队（official 本就无
   scoring 段，宁缺毋滥），等真实事务公文样本扩充后再拟合。
3. gen2026/official.jsonl 已入库 `_qa/corpus/`（gitignore），可复现：
   `python tools/gen_samples.py --scene official`

## 复现

```
python tools/gen_samples.py --scene official
python tools/adversarial_eval.py --attacker llm   # 对抗评测见同目录报告
```
