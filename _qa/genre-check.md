# 文种切片测量与判定（v0.20.0）

判定线 0.873（= official 基线 0.923 − 0.05，预声明于 design.md §4）。
切片按线上判据 `ood.detect_genre`；<8 句不入样。
语料：AI gen-oos + gen-cal（86 篇过门槛）· 真人四省门户（75 篇过门槛）。

## 印发（issuance-notice）

样本：AI 46 · 真人 29（判据 `ood.detect_genre` 切片）

AI 分数中位 29 · 真人 63（p90 89）

冻结系数同文种 AUROC：**0.464**（判定线 0.873）

未达线 → 文种条件逻辑回归分层 5 折 CV：**0.638**；渠道留一 LOCO：**0.311**

判定：**未全过 → 建议出分抑制**（score=None + 说明行）


### 按模型

| 模型 | n | 中位 | ≥80 |
|---|---|---|---|
| deepseek-chat | 9 | 29 | 0% |
| deepseek-reasoner | 10 | 32 | 10% |
| doubao-seed-2.0-lite | 5 | 15 | 0% |
| doubao-seed-2.0-pro | 2 | 27 | 0% |
| doubao-seed-2.1-pro | 3 | 22 | 0% |
| glm-4.7-flash | 3 | 32 | 0% |
| glm-5.3-flash | 5 | 58 | 0% |
| kimi-k3 | 5 | 29 | 20% |
| minimax-m3 | 4 | 20 | 0% |

### 按真人渠道

| 渠道 | n | 中位 | p90 | ≥80 |
|---|---|---|---|---|
| www.ah.gov.cn 省政府文件 | 1 | 92 | 92 | 100% |
| www.hubei.gov.cn/zfwj | 18 | 76 | 89 | 44% |
| www.hunan.gov.cn swszf | 10 | 0 | 73 | 0% |

## 批复（approval-reply）

样本：AI 36 · 真人 35（判据 `ood.detect_genre` 切片）

AI 分数中位 75 · 真人 2（p90 93）

冻结系数同文种 AUROC：**0.791**（判定线 0.873）

未达线 → 文种条件逻辑回归分层 5 折 CV：**0.895**；渠道留一 LOCO：**0.448**

判定：**未全过 → 建议出分抑制**（score=None + 说明行）


### 按模型

| 模型 | n | 中位 | ≥80 |
|---|---|---|---|
| deepseek-chat | 7 | 44 | 29% |
| deepseek-reasoner | 6 | 50 | 17% |
| doubao-seed-2.0-lite | 3 | 85 | 67% |
| doubao-seed-2.0-pro | 4 | 83 | 75% |
| doubao-seed-2.1-pro | 3 | 72 | 33% |
| glm-5.3-flash | 3 | 96 | 67% |
| kimi-k3 | 7 | 84 | 57% |
| minimax-m3 | 3 | 47 | 33% |

### 按真人渠道

| 渠道 | n | 中位 | p90 | ≥80 |
|---|---|---|---|---|
| www.hubei.gov.cn/zfwj | 5 | 91 | 93 | 60% |
| www.hunan.gov.cn swszf | 15 | 0 | 5 | 0% |
| www.sc.gov.cn 川府函 | 15 | 30 | 98 | 27% |

## 判据 × 标题对照

| 文种 | 判据切片 | 其中标题同文种 | 标题切片但判据未中 |
|---|---|---|---|
| 印发 | 29 | 29 | 4 |
| 批复 | 35 | 35 | 1 |
