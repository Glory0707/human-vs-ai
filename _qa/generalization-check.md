# 泛化体检：样本外独立验证（现行系数冻结）

拟合集（gen2026 九模型 + 知乎回答 + gov.cn/部委/广东公文）与样本外
（四模型 + 豆瓣/果壳 + 湖北/四川公文）零重叠，评分只测不调。
工具：`tools/oos_check.py`（可复现）。判定线：掉幅 <0.05。

| 场景 | 样本外 AUROC | 基线 | 判定 |
|---|---|---|---|
| general | 0.923 | 0.935 | PASS |
| official | 0.879 | 0.934 | FAIL |

## 结论

- **general 通过**（0.923，掉 +0.012）：换模型（四个样本外模型）+ 换渠道（豆瓣/果壳）双重位移，deepseek-chat 漏检偏高延续
  "平价/旧代模型更人味"的已知规律，被统计底盘兜住。
- **official 不通过**（0.879，掉 +0.055）。印发/批复文种已由 genre_scoring 抑制出分（v0.20.0），剩余误报集中在省门户
  的表彰/决定类公文（见按真人渠道表）——official 系数绑定事务文种，
  跨文种出分是已知边界；决定/表彰类文种扩展校准在 design.md §4 排队。

## general 问答域样本外（general profile，冻结系数）

样本：AI 27（deepseek-chat/deepseek-reasoner/doubao-seed-2.0-pro/glm-4.7-flash）· 真人 78（douban-movie-review/guokr-article）

| 指标 | 样本外 | 拟合基线（auroc_holdout） |
|---|---|---|
| AUROC | **0.923** | 0.935 |
| 掉幅 | +0.012（允许 <0.05） | — |
| 真人 p50 / p90 | 1 / 74 | 2 / 77 |

判定（≥0.885）：**PASS**

### 按模型

| 模型 | n | 分数中位 | ≥80 占比（漏检） |
|---|---|---|---|
| deepseek-chat | 8 | 75 | 62% |
| deepseek-reasoner | 8 | 96 | 12% |
| doubao-seed-2.0-pro | 8 | 84 | 38% |
| glm-4.7-flash | 3 | 90 | 0% |

### 按真人渠道

| 渠道 | n | 字数中位 | p50 | p90 | ≥80 误报 |
|---|---|---|---|---|---|
| douban-movie-review | 51 | 1309 | 22 | 77 | 10% |
| guokr-article | 27 | 2902 | 0 | 1 | 0% |

## official 公文域样本外（official profile，冻结系数）

样本：AI 25（deepseek-chat/deepseek-reasoner/doubao-seed-2.0-pro/glm-4.7-flash）· 真人 35（www.ah.gov.cn 省政府文件/www.hubei.gov.cn/zfwj/www.hunan.gov.cn swszf/www.sc.gov.cn 川府函/www.yn.gov.cn zcwj）

| 指标 | 样本外 | 拟合基线（auroc_holdout） |
|---|---|---|
| AUROC | **0.879** | 0.934 |
| 掉幅 | +0.055（允许 <0.05） | — |
| 真人 p50 / p90 | 28 / 82 | 4 / 58 |

判定（≥0.884）：**FAIL**

### 按模型

| 模型 | n | 分数中位 | ≥80 占比（漏检） |
|---|---|---|---|
| deepseek-chat | 7 | 81 | 43% |
| deepseek-reasoner | 8 | 85 | 38% |
| doubao-seed-2.0-pro | 7 | 90 | 14% |
| glm-4.7-flash | 3 | 75 | 67% |

### 按真人渠道

| 渠道 | n | 字数中位 | p50 | p90 | ≥80 误报 |
|---|---|---|---|---|---|
| www.ah.gov.cn 省政府文件 | 3 | 3485 | 45 | 59 | 0% |
| www.hubei.gov.cn/zfwj | 2 | 2656 | 40 | 40 | 0% |
| www.hunan.gov.cn swszf | 4 | 1458 | 82 | 100 | 75% |
| www.sc.gov.cn 川府函 | 4 | 1480 | 44 | 54 | 0% |
| www.yn.gov.cn zcwj | 22 | 2584 | 11 | 65 | 5% |

### 同文种对照

v0.20.0 起移交 `tools/genre_check.py`（产出 `_qa/genre-check.md`）：
判据切片、分层 5 折 + 渠道留一双道验证、判定线三分支都在那里。

