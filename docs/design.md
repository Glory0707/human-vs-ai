# human-vs-ai 项目设计

> 当前版本见 human_vs_ai/__init__.py。已实现能力见 [README](../README.md)，本文只保留定位、原则、取舍与未竟事项。

---

## 1. 一句话定位

**本地优先、可解释的中文 AI 味分析器：逐句指出哪里像模板、为什么像、怎么改——不判 AI 概率。**

四个差异点：

1. **可解释** —— 每处命中带规则名、语言学解释、修改方向、研究出处；商业工具给分数不给解释，我们反过来。
2. **过评测的规则** —— 每条规则带着 C-ReD/HC3 上的区分度数字；证伪即删，砍掉清单防复活。
3. **本地优先** —— 零网络调用，文本不上传；唯一硬依赖 PyYAML。
4. **按场景组织** —— 词表规则分七 profile（学术/作文/问答/新闻/公文/口味/短评），统计底盘共用；实测证明跨文体复用词表必然失效。

## 2. 设计原则

1. **推断不是判定**：命中≠AI。报告措辞永远是风格提示；免责声明随每次输出走。
2. **弱规则必须共现**：`low` 级规则全文 ≥2 处命中才升为正式发现，孤立命中进参考区。
3. **规则必须过评测**：没有区分度数字的规则不进库；评测脚本（tools/evaluate_*.py）是规则库的看门人。
4. **可溯源**：每条规则给解释与出处（维基/humanizer/社科院/人大编码/实证论文），`explain` 命令可查全文。
5. **诚实报告口径**：统计指标样本不足（<3 句）不判；切分口径全文唯一（字级 2-gram，跨端可复现）——宁可不判，不给不可比的数字。
6. **界面减法**：同一句多规则命中聚合一节，不重复贴原句；删掉这行字用户没损失就删。
7. **做减法**：每轮规划先问"哪些该砍"。

## 3. 砍掉清单（防复活）

| 砍掉项 | 理由 | 实测依据 |
|---|---|---|
| AI 生成概率分数 / "能过知网"承诺 | 检测器不可靠是 OpenAI/斯坦福/法院/Nature 的共同结论；给分数等于自欺 | OpenAI 2023-07 下架 classifier；斯坦福 61% 误判；Newby v. Adelphi 判"devoid of reason" |
| S-TRIAD-01 三连排比（学术 profile） | 学术摘要是真人的方法条件列举 | C-ReD 区分度 -0.17；问答文体 +0.22 有效，留待 general |
| D-TTR-01 词汇丰富度（学术 profile） | 方向随文体反转，单一方向判定会误导 | C-ReD：AI 0.715 **>** 真人 0.649（真人摘要术语重复是精确性）；HC3：AI 0.610 **<** 人类 0.696 |
| L-SAFE-01 万金油对冲 | 零区分度 | C-ReD ±0.00 |
| D-NGRAM-01 4-gram 复现（official profile） | 公文本身就是套语复现文体，真人 0.24–0.59 与 AI 无区分度 | 首轮公文评测 100% 误报的主因之一 |
| 自动改写 | 改写是 humanizer 类 skill 的地盘；linter 管检测+解释，两者是上下游不是竞品 | — |
| 训练二分类检测模型 | 黑盒，撞车学术界，且 SHAP 研究证明事后归因不可靠 | 计算机应用与软件 2024：黑盒归因关键词多为数字/生僻字 |
| 在线服务 / API 优先 | 与本地可解释定位自相矛盾；隐私是刚需不是卖点 | — |
| 单一"AI 味总分"（黑盒） | 黑盒分数会滑向"AI 率"判决语义；v0.10.0 起的指数可拆解构成、锚定真人分位，语义不同 | — |

## 4. 排队事项（按需启动）

- **P2**：~~general 当代长文评分~~（v0.17.8）· ~~official 评分拟合~~（v0.17.8；v0.18.1 体检定位文种边界）· ~~official 文种域外提示~~（v0.19.0）· ~~文种内二次校准~~（v0.20.0 定案两文种出分抑制——批复分层 CV 0.895 看似可行但渠道留一 0.448，模型背下的是渠道风格而非作者信号，详见 rules.md §8；未来拿到跨省配对语料可重新申报）· 剩余：**真人侧语料持续积累**（collect 入口）、general/official 系数随漂移信号迭代
- **P3**：~~规则 era 自动化挖掘~~（v0.24.0 `tools/era_remine.py`，季度重跑看漂移；漂移监测 v0.17）· ~~句级困惑度~~（v0.23.0 可选依赖；v0.24.0 1.7B 重标定案弱信号默认关闭——区分度 0.633→0.657，规模 ×2.8 仅 +0.024）· 观点反复解释检测（需语义相似度，n-gram 只能部分覆盖）· 剩余：**era 报告驱动的新指纹入表**（候选须人工 grep 验证语境，走正式校准轮）

## 5. 架构（已验证）

```
CLI（argparse，七个子命令：check / diff / collect / stats / rewrite / explain / profiles）
        │
引擎 engine.analyze() ── 规则库 YAML（七 profile）+ 统计 stats + 切分 segment
        │                   ├ ood.detect（域外文体：文言/诗行 → 报告随行提示）
        │                   └ para_heat（段落热度：混写文本定位哪几段最像 AI）
        │
报告 report（terminal ANSI / markdown / json，同一份内容多出口；
        sarif / html 由 sarif.py、htreport.py 供 CLI 直接调用）

网页版：web/index.html（单文件；engine.js 同构引擎 + render.js 共享渲染层 +
规则 JSON 注入；tools/build_web.py 构建，tools/check_web_consistency.py 守护）

VS Code 扩展：vscode-extension/（复用 engine.js/rewrite.js/render.js 与规则 JSON，
tools/build_vscode.py 注入；命令 analyze + rewrite 各一个 Webview 面板，
node smoke-test.js 冒烟）

Obsidian 插件：obsidian-plugin/（同一套注入，tools/build_obsidian.py 生成
main.js/manifest.json；侧边视图 + 命令 + 设置页，node smoke-test.js 冒烟）

校准工具链（v0.17）：tools/gen_samples.py（大模型 API 生成当季评测语料，
key 外读不入库）· tools/adversarial_eval.py（对抗自评测：改写器/LLM 当
攻击者）· tools/drift_monitor.py（collect 样本分布漂移监测）
```

工程纪律：零网络调用；切分口径全文唯一且跨端可复现（字级 2-gram，不依赖任何分词库，v0.11.0 起 jieba 退场）；规则阈值全部放 YAML 不进代码（校准只改数据）；报告渲染与引擎解耦（JSON 是唯一事实源，terminal/md 都是它的投影）；双实现不许独立演化（一致性测试是网页版的发布门）；收尾纪律：版本号等源码改动必须先于构建，推送后确认 CI 绿（gh run list）才算结束——CI 的构建产物新鲜度守护（重新生成后零 diff）会抓住"先构建后升版本"的顺序错误，v0.17.4/5 实证。

## 6. 验证基线（当前值，复现命令见 README「开发」）

- **单元测试**：202 项（切分/统计/引擎/边界/评分/口味与改写/格式/多文体 profile/域外与漂移/模糊回归/私库——私库层缺语料自动跳过）
- **C-ReD paper 校准**（真人 80 vs deepseek-v3/qwen-3/gpt-4o/deepseek-r1 各 80）：词表句均命中真人 0.051 vs AI 0.254–0.407，AUROC **0.888**（v0.25.0 era 指纹入表前 0.804）；句长 CV 真人 0.483 vs AI 0.274–0.383（四模型全低），AUROC **0.799**；deepseek-r1 最难检
- **HC3-Chinese 校准**：词表 AUROC 0.476（学术词表在问答文体失效——profile 分治的实证）；CV 0.763；字级 2-gram TTR 0.684
- **长度分档**：真人 CV p50 短/中/长 = 0.467/0.494/0.520，D-UNIF 三档阈值 0.30/0.33/0.37（数据 `_qa/length-tiers.md`）
- **公文**：87 篇口径真人误报率 16/87 = 18.4%（验收 <20% PASS，余量 1.6pp）；官方词表当代验证组合覆盖 9/9 模型、真人 0 误伤
- **fixture 冒烟**：AI 样本 20 处命中（高 4）vs 人类样本 0 高 0 中（tests/data/）
- **多文体扩展（v0.14）**：essay/news 评分建立（C-ReD 全量类平衡）；v0.26.0 era 指纹入表重拟合后 **essay 全量 0.954/留出 0.953、news 0.951/0.948**；review 短评词表层不出分——详见 rules.md §9
- **当代验证（v0.17）**：essay 全量重跑 AUROC **0.942** / news **0.933**（按模型分解：qwen-2.5/claude/gpt-4o 召回 95%+，gpt-3.5 旧代仅 53%）；general 词表双代际验证（C-ReD QA 全量 + gen2026 当季 9 模型 69 篇：三连排比 83% 命中仍是当代最顽固指纹）；official 词表初步验证（9 模型 AI 公文组合覆盖 100% / 真人 0 误伤）；**域外探测**判据 C-ReD 全量 10.4 万篇校准（正样本 5/5、误报 0.005%）；**对抗自评测**（LLM 洗稿削词表 89% 后仍 93.3% 超阈值——统计底盘扛住定向规避）——详见 rules.md §9 与 _qa/*.md
- **引擎性能**：7.1 万字 Py 140ms / JS 34ms（min-of-N，2026-09 复测，含 ood/para_heat）
- **双引擎一致性**：**427 项**逐字段 diff 全绿 × 7 profile（含 ood/para_heat/清理稿 apply 对拍与 gov_yinfa/gov_pifu 文种门控）；对抗探针含 emoji 码点/孤立低代理/行分隔符全集/闭引号吸收/未闭合围栏/双竖线表格/引号不配对/邮箱/括号洪水
- **当代评分重拟合（v0.17.8）**：official 全量 0.957 / 留出 ×10 均值 0.923（真人事务公文 71 vs gen2026 当季公文 70，ngram/连接词在公文文体反向、模型转负权重）；general 全量 0.945 / 留出 0.935（知乎真实长回答 110 vs gen2026 69；旧 HC3 系数在同期语料仅 0.602≈失效，重拟合动机）——详见 rules.md §8/§9 与 _qa/official-contemporary.md
- **泛化体检（v0.18.1，冻结 v0.18.0 系数只测不调）**：general 样本外 **0.923**（豆瓣/果壳真人 78 vs 未参拟合四模型 27，掉幅 0.012 PASS）；official 样本外 **0.731**——AI 侧分布正常（无特征漂移），真人不达标是**文种边界**：省级门户"印发类"（正文=规划全文附录，p50=76）与"批复"（p90=98）真人侧大面积高分，同文种对照印发类 AUROC 0.294 反转、批复 0.614 近随机——详见 _qa/generalization-check.md
- **口味校准层**：personal 12 条口味条目 + rewrite；被毙稿召回 31/31、定稿误报 0/37、改写维度 5/6；`tools/check_private_leak.py` 守护边界

### 轮次日志（细节见 [plan.md](plan.md) 轮次注记与 [rules.md](rules.md) §8）

| 轮次 | 要点 |
|---|---|
| v0.28.6 | 文档收敛轮（全仓 md/配置）：陈旧数字统一——rules.md §8/§9 essay/news 系数行补 v0.26.0 重拟合后现役值（0.954/0.953、0.951/0.948）与分位锚点、README/design 测试与一致性计数（202/427）、CI 步骤名同步；README 实测区分度表 14→5 行去 README/design/rules 三方重复（指标级细节单点收敛 rules.md，关键单指标并入注记行）；design.md 排队事项划线项收敛为名字+版本（教训细节单点在 rules.md §8）、v0.17 前老轮次日志压到结论级；研究文档尾部过时'下一步'改落地注记；_qa/*.md 为时点评测证据、plan.md 为里程碑历史，均不动 |
| v0.28.5 | 二轮端到端走查（补未覆盖面）：键盘 Tab 序列全控件可达+Enter 激活（印章彩蛋为装饰性鼠标专属，不占 Tab 焦点）；窗口 resize 前后滑块逐像素对齐；粘贴 3 万字/纯英文/reduced-motion 仿真不崩；VS Code 面板暗色形态与 CLI HTML 报告首次视觉审查。修 1 处三端共有暗色缺陷——命中波浪线 SVG 描边硬编码亮色朱砂 #b8462e，暗色下发闷，web/VS Code/Obsidian 三端暗色整条换 #d9765c（data-URI 吃不到 CSS 变量，DPR3 截图复验对比度） |
| v0.28.4 | 端到端走查轮（Playwright 模拟真实用户 + DPR2-3 视觉审查）：30 项用户流程全过——首访空态/样例/手动输入/七档切换/短文门槛/主题与档位持久化/段落热度与发现卡点击定位/复制 Markdown/分割线拖动·双击复位·键盘微调/清空撤销/匿名样本导出（blob 截获验形状）/印章彩蛋/改写锁定口味/过滤器三档/清理稿两态复制/文言与诗行域外/印发文种抑制/拖拽入稿（含非 md 拒绝）/移动端 390px 无溢出/CLI 全命令（json/sarif/csv/html/md/docx/odt/批量/门禁退出码/错误路径）。修 3 处——①单文件 `-f csv` 裸抛 ValueError（补齐为单行批量汇总+回归测试）；②清空后空态滞后 300ms（显式动作不走防抖）；③personal 档解释文案把内部事件哈希带进三端 UI（构建剥离标记补'（事件 '形态，产物复查 0 残留） |
| v0.28.3 | 测试员轮（找 bug→直接修）：26 组病态输入（空/纯标点/孤立代理/  等行界/未闭合围栏/正则回溯面）双端逐字段对拍**零漂移**；修 5 处——①engine 数值字段 null 容错（YAML 显式 `doc_threshold:`/`intercept:` 空值原会 float(None) 炸报告路径，_num 容错落默认+2 条回归测试）；②Obsidian 视图恢复 mode=rewrite+profile≠personal 错配守卫（改写会跑错规则库）；③web 分段滑块在字体异步加载后错位（fonts.ready 重摆）；④拖入文件读取失败静默无反馈（补 toast）；⑤清空后残留清理稿视图状态（复位到建议列表）；前端竞态实测（32k 长文分析中连打换档/切模式/清空/撤销，清理稿视图清空复位）零 JS 错误 |
| v0.28.2 | 目录与文案收敛轮（四端）：删 1.3MB 会话截图缓存/pytest·ruff 缓存/egg-info/一致性检查中间产物（根目录仅剩 11 项；174MB 校准语料与模型权重为评测资产保留）；用户可见文案减脂——统计行去'规模/节奏/词汇'前缀（连接词密度→连接词、4-gram 重复率→重复率，Python 与 JS 两侧同步）、复制按钮统一'复制'（格式由上下文自明）、toast 去宾语（已复制/已导出）、顶栏'纯本地 · 文本不上传'→'纯本地'、印章悬浮'校准语料真人分数：'→'真人'、清理稿视图说明压到'删 N 行 · 改 N 行'、插件草稿块摘要去'草稿·选中即可复制'并删整行尾注、Obsidian 三种空态归一'未打开笔记/笔记为空'、collect LABELS 去括号解释、CLI 终端头去'profile'；VS Code 内联统计块漏同步在预览截图复核中抓出补齐；断言钉死的产品语（'构成/未发现模板化写作/风格提示/重要数据和结论要保留'等）原样保留 |
| v0.28.1 | 冗余清理轮（零功能变化，四端输出逐字节不变）：三端重复的发现卡/建议行组装收编进 render.js 共享层（findingsHtml/adviceRowsHtml，web/VS Code/Obsidian 各 ~40 行拷贝归一）；load_scoring 加 lru_cache（批量扫描不再每篇重读 YAML，200 篇 0.8ms/篇）；DocStats.sentence_cvs 死字段移除（算而无人读，JSON 本就不含）；htreport 弱命中上限复用 report.HINTS_MAX（原写死 12）；过时/叠放注释清理（render.js 三端口径叙述、template.html 同义双注释）；ruff F 级与 tools 死脚本扫描零命中（脚本↔_qa 产物对应全保留） |
| v0.28.0 | 四端打磨扩展轮（高分辨率视觉审查驱动）：**改写建议闭环补完**——清理稿（apply_edits/applyRewrite 双端同构，advice.line 行号对位，删档删行/改档换候选/其余原样，CRLF 保真）；CLI `rewrite --apply`、网页「清理稿」视图（复制清理稿/返回建议双态按钮）、VS Code 与 Obsidian 面板底部清理稿折叠块、Markdown 导出随「清理稿（草稿）」段；一致性探针 +105 项 apply 对拍；VS Code 报告改同句聚组（buildGroups，与网页/Obsidian/CLI 对齐）；交互打磨=换档滚动复位+toast 滑入；README 补 v0.26.0 漏更的 essay/news 留出数字；8.6 万字 2.5s 全流程 + 竞态零 JS 错误复测；11 张 DPR2 截图审查（亮/暗/移动/四端）零排版缺陷 |
| v0.27.0 | general 砍词尝试证伪轮（否定结果入档）：era 问答域 14 条"退化"触发逐 pattern 四语料测量（gen2026/zhihu/CReD QA 双侧），砍人侧反超与双零 pattern + 同协议重拟合后拟合语料 0.950/0.937 看似可行，**OOS 0.923→0.846 跌破 0.873 线，证伪撤销**——小样本重拟合的系数配比过拟合知乎人设化分布，raw 粘贴文本的判别力寄生在旧系数×旧 pattern 组合上；词表与系数维持 v0.18 定案，OOS 0.923 复测确认；证据与重开条件 `_qa/general-pattern-baselines.md`、rules.md §9 |
| v0.26.0 | era 指纹入表第二波（news+essay 重拟合）轮：news 新增 N-BUZZ-01 反响膨胀套话（doc 命中 AI 15.7% vs 真人 0.1%）、N-HEARSAY 扩分析人士/数据显示/接受采访三族（1.7% vs 12.8%）；**N-RECENT 移除"日前"**——era 全量复测真人 8.5% vs AI 2.6% 真反转（THUCNews 正体消息语，2026 模型不用），v0.14 的 250 篇抽样口径错判，入砍掉清单；news 评分重拟合 0.938/0.935→**0.951/0.948**，essay 补重拟合 0.952/0.951→**0.954/0.953**（v0.25.0 入表后的既定流程补课） |
| v0.25.0 | era 首轮真指纹入表轮：L-AIM-01 研究陈述八股（medium，4 pattern 族全 corpus 验证——研究旨在 52% vs 2.4%、结果陈述族 15.7% vs 1.3%、理论/实验依据 7.5% vs 0.1%）+ essay E-FORM/E-SUB 各扩 2 变体（信息爆炸/快节奏族、找到属于自己的/在人生的道路上）；C-ReD paper 词表 AUROC **0.804→0.888**（L-AIM doc 命中 +0.56 现役最强），HC3 问答零误伤，引擎级 essay E-SUB 10x；边缘候选（具有重要意义 5x 贴线）如实不入；漂移对拍确认入表前后现役规则零漂移 |
| v0.24.0 | era 重挖 + PPL 重标收尾轮（P3 双项定案）：①`tools/era_remine.py`——C-ReD 五域挖 AI 过采样短语（3-8 字极大短语，重叠折叠保留最长，覆盖差 >2x 各自保留）+ 现役词表逐 pattern 体检（doc 覆盖比口径同引擎 re.search，双侧 <5 篇"样本不足不判"），报告 `_qa/era-remine-*.md` 只报告不动词表；首轮 paper 域候选含"本研究旨在探讨"45x/"结果显示"63x 级真指纹，体检 12 条退化待人工 grep 复核。②句级困惑度 1.7B 重标（Qwen3-1.7B-Base）：区分度 0.633→0.657，规模 ×2.8 仅 +0.024 对规模响应平缓，放大不现实——**弱信号定案默认关闭**，"待更大模型重标"关闭；`_qa/ppl-calibration.md` 改双模型对比 |
| v0.23.1 | PyPI 发布就绪轮（无引擎变化）：license 迁移 SPDX `License-Expression`（setuptools>=77，消构建弃用警告）+ 补 `project.urls`（Homepage/Source/Issues 进 PyPI 侧边栏）；新增 `tools/pkg_check.py` 发布自查——构建 → wheel/sdist 内容审计（7 规则 YAML、入口脚本、无杂物）→ twine check → 临时 venv 装 wheel 冒烟（profiles/check/stats/explain/ppl 缺依赖指引）→ 同 venv 改装 sdist 验证源码分发重建路径，全绿 |
| v0.23.0 | 句级困惑度轮（P3）：human_vs_ai/ppl.py teacher-forced 句级 NLL（滑窗长句、nll_to_stats 纯函数口径），CLI `ppl` 子命令懒加载可选依赖（torch/transformers 进 [ppl] extra），tools/ppl_calibration.py 语料标定——gen-oos AI vs 豆瓣/果壳真人，doc 特征最强区分度 0.633（Base 底座）/ 0.620（Instruct），方向正确（AI median 177 vs 真人 213）但强度不足，结论=可选弱信号默认关闭；D:\chat 本地 GGUF（spark2_5 自定义架构 / qwen35 9B 超 17GB 内存）进不了 transformers 路径，模块按 --model 任意 HF 路径设计即插即用；标定权重 Qwen3-0.6B-Base（hf-mirror，_qa/models/ 不入库） |
| v0.22.0 | 事务文种样本外夯实轮：scrape_genre_corpus 扩云南 zcwj 静态档案源（--limit 上限；正文含印发/批复标题跳过——抑制出分进不了切片）+ 事务文种入库，官方样本外切片 n=11 → 35（五省渠道），AUROC 0.873 贴线 → **0.888 PASS**（掉幅 0.035）；general 0.923 不变 |
| v0.21.3 | 测试员轮：74 组病态输入对 Py 全出口与 JS 引擎双端模糊，零崩溃（段落热度除零守卫已存在）；性能复测 JS 87k 字 54ms。修两个真问题——匿名样本导出在防抖窗口内拿新输入配旧分析（lastAnalyzedText 配套原文）、拖入 >5MB 文件无守卫；24 样例模糊回归网进 test_edges。随后 2x DPR 端到端走查 18 项用户流程 + visual-judge 超高清验收 9/9 pass，零产品 bug |
| v0.21.2 | 目录与文案收敛：删无引用的 _qa/eval-{cred,hc3}.json 并入 ignore；文案续收（空态标题/字数提示/拖拽 toast/悬浮/未出分说明/域外行/Obsidian 设置说明，引擎双端与断言同步） |
| v0.21.1 | 冗余清理（12 份 CLI 出口快照逐字节不变）：report 私有名转公开，免责/特征标签常量与读取异常处理去重；buildGroups/statsRows/reportToMarkdown/adviceToMarkdown 收编进共享层 render.js，web 复制 Markdown 补齐缺失的域外行；卡号脱敏正则与 CLI 对齐 |
| v0.21.0 | 全功能打磨（visual-judge 驱动）：修 --fs-s 坏 token；未出分改虚线印章（三端收编）；发现卡原句点击定位原稿；弱命中折叠动画；实测 8.7 万字 127ms 无渲染炸弹；复审 10/10 pass |
| v0.20.0 | 文种出分抑制定案（预声明判定线先于测量提交）：语料扩量（湖南/安徽真人、gen-cal 七模型），genre_check 冻结/分层 CV/渠道 LOCO 三数齐测——印发 0.464 无信号、批复 0.791 但 LOCO 0.448 证伪为渠道指纹，两文种均 genre_scoring 抑制；修 Obsidian Markdown score_note 字段名 bug；官方样本外语义收敛为事务文种 |
| v0.19.0 | 文种域外提示：ood.detect_genre 结构短语判据（印发/批复各 100% 召回、拟合集 87 篇零误报），genre_ood 开关放 scoring 段保双端同构；报告按文体/文种分行；一致性 322 项；eval-official 重生成（87 篇误报 18.4%，余量 1.6pp） |
| v0.18.1 | 泛化体检（冻结系数只测不调，语料四路零重叠）：general 样本外 0.923 PASS 获 README 背书；official 0.731 定位文种边界——同文种对照印发 AUROC 0.294 反转、批复 0.614 |
| v0.18.0 | official/general 当代评分双落地（留出 0.923/0.935，旧 HC3 系数当代仅 0.602）；真人语料扩充（公文 87 篇/知乎 110 篇）；computer-use 走查 14 项零 bug |
| v0.17.8 | 文档审计轮：plan.md 精简（删 v0.9-v0.12 时代 15 段过程注记——与轮次日志重复，121→73 行；排队清单收敛为 design.md §4 单一来源）；README/design/rules/taste 四处"四场景/三个 profile/161 项"陈旧口径统一为现状；rules.md 游离表格残片并入正文、§7 补漂移监测上线；配置文件核查无冗余 |
| v0.17.7 | 测试员轮（164 项测试）：修 2 个 bug——①collect.sanitize 卡号正则只认 16-19 位，22 位长数字串（订单号等）整段漏打码，改 16 位起整段打码（脱敏宁枉勿纵）+ 回归；②drift_monitor 目录输入抛 PermissionError traceback，抽 resolve_inputs 跳目录并干净报错 + 回归。排查无恙层：Py/JS 15 组对抗探针 ood/para_heat/excerpt 逐字段一致；CLI 黑盒 10 组边界（GB18030/二进制/空文件/不可写输出/不存在规则与场景/空 stdin）全过；web 竞态守卫（clearTimeout+analyzeGen 代际+双 rAF）与 Obsidian 同步 analyze（读当前活动文件，无错位）确认完备 |
| v0.17.6 | 文件与文案清理轮：删本地产物（.playwright-mcp/.pytest_cache/egg-info/__pycache__/_qa 截图与运行缓存，语料与私人数据不动）；文案收短——域外行"超出评测语料范围，指数与统计仅供参考"→"指数仅供参考"、热度行去"（命中密度/句）"括注（定位交互改悬浮提示）、collect 输出与 help 若干条收短、空态句号统一 |
| v0.17.5 | 冗余清理轮（零功能变化）：rewrite.py 死映射 _VOICE_RULES 删除（口味编号实际来自 personal.yaml 的 taste 字段）；render.js 纯内部叶子（compsHtml/OOD_NAME/SCORE_LABEL）移出导出表、vscode 幽灵解构 componentsText 移除；标点正则收敛单一事实源（stats.PUNCT，ood.py 删本地拷贝改导入——口径一致从人肉同步变结构保证）；ood.py 常量统一私有命名、drift_monitor NaN 判断收敛 finite()；test_ood 尾部 helper 归位 |
| v0.17.4 | 全端打磨轮：段落热度可点击——原稿自动选中对应段落首句（excerpt 定位，引擎 Py/JS 同构新增字段）；域外判定改全文+逐段聚合（白话引用文言段时全文统计被稀释致漏检，C-ReD 全量复测误报率不变）；构成列 HTML 版每项 nowrap 修手机端"标签 数值"拆行（visual-judge 抓出）；性能复测 7.1 万字 Py 140ms / JS 34ms 无回退，ood 正则预编译 |
| v0.17.3 | T6 收尾：.stats .row 特异性覆盖 .ood-note 致暗色域外提示退化灰字——三端选择器提升（visual-judge 抓出）；对抗评测双攻击者合并报告（LLM 洗稿削词表 89%、93.3% 仍超阈值——统计底盘扛住定向规避）；10 状态高分辨率视觉走查 9 pass / 1 截图脚本失误 |
| v0.17.2 | 校准机制化轮：tools/adversarial_eval.py 对抗自评测（改写器/LLM 双攻击者）；tools/drift_monitor.py 漂移监测（collect 样本按 profile 聚合对比基线，p50≥15 分/规则≥10pp 信号）；official 场景 gen2026 公文 70 篇初步验证（组合覆盖 100%/真人 0 误伤） |
| v0.17.1 | 段落热度轮：compute_para_heat 每段加权密度（与全文 hit_density 同口径，level 三档），混写文本定位"哪几段最像 AI"，四端同行展示；density 保留全精度（Py banker's vs JS half-up 漂移规避） |
| v0.17.0 | 当代验证+域外+锚点轮：general 词表双代际当代验证（C-ReD QA 全量 + gen2026 当季 9 模型 69 篇，D-DASH 问答域反向砍掉）；域外文体探测器 ood.py + JS 同构（文言×低"的地得"×零"了"三信号、等长对句诗行，四端随行提示）；四端分数读数语言（"超过 90% 校准真人"，p50/p90 移入悬浮）；tools/gen_samples.py 大模型 API 语料生成器（key 外读） |
| v0.16.3 | 修 4 个边界 bug：batch 字面路径优先于 glob、web 拦文件拖放默认行为、QuickPick Esc 不再偷偷分析、collect 空文本守卫；7 场景 × 19 组模糊 0 炸 |
| v0.16.2 | 删中间文件；全端文案收短 |
| v0.16.1 | render.js 死叶收敛、死 CSS 清理、pyflakes 清零并入日常自查 |
| v0.16.0 | 弱命中三端折叠；VS Code webview 对齐设计语言（修档位变量缺失/CSP 拦 data 图）；移动端修 7 项溢出；引擎 10 万字 112ms |
| v0.15.0 | Obsidian 插件落地（构建注入四端同源；命令+侧边视图+800ms 自动重析+设置页；冒烟 14 项） |
| v0.14.1 | 分隔线拖动（双击复位/方向键/记忆/限幅）；清空撤销 toast；实时字数；拖稿入栏；「看个例子」；场景/模式记忆；滑块按真实几何定位 |
| v0.14.0 | C-ReD 五域接入，新增 news/essay/review 三 profile（news 留出 0.935、essay 0.951，v0.26.0 重拟合后刷新为 0.948/0.953）；collect 脱敏导出；词表挖掘/侦察/拟合工具三件套；砍掉清单入 rules.md §9 |
| v0.13.2 | Logo 重设计：印章框内一行字迹——左半手写波浪（人）右半拉直（AI），favicon/顶栏/空态同步 |
| v0.13.1 | 网页设计语言对照 eggpaper 重写（token 同源/批注卡/波浪线/toast/亮暗/印章交互；移动触控目标 44px）；htreport 同步 |
| v0.13.0 | docx/odt 输入；批量扫描 csv/json；diff 改进闭环；--fail-above 门禁；SARIF/html 出口；网页「文纸·朱批」首版 |
| v0.12.4 | 修重复句分组标题爆炸（web/md/CLI 三处去重） |
| v0.12.3 | 三类双端漂移修复（孤立低代理/splitlines 行界全集/句尾闭引号吸收）；网页大文本 rAF 代数守卫；一致性 156 项 |
| v0.12.2 | 删 VERSION/_server_deps 死文件；rules.json 剥离校准注 |
| v0.12.1 | auroc 四份副本收敛单实现；逻辑回归收敛 fit_score_tiers.fit |
| v0.12.0 | 评分长文分档（全量 4993 篇，长档 holdout 0.975 vs 全局 0.946）；动效系统；auroc 并列 NaN 死循环修复；CI 上线 |
| v0.11.3 | 对拍 48 探针抓三类真 bug（码点口径/空均值/正则回溯 51.4s→0.11s）；CLI 干净报错；一致性 136 项 |
| v0.11.2 | 文案减负；目录清理 |
| v0.11.1 | 修扩展 activate 即崩；web/render.js 共享渲染层；load_rules 缓存 |
| v0.11.0 | jieba 退场，TTR 口径全文统一字级 2-gram（跨端可复现）；general 类平衡（0.838/0.870）；指数整数化 |
| v0.10.0 | 四臂消融驱动综合评分落地（消融数字见 rules.md §8） |
| v0.9.1 | 短文本只留规模行等三处减法 |
| v0.9.0 | MATTR 滚窗 O(n)；正则缓存；暗色模式；命中高亮；扩展波浪线装饰；stdin |
| v0.8.0 | 切分块模型（列表/表格入分析、URL/邮箱剥离）；personal 关怀腔泛化；网页中文化+改写模式 |
| v0.7.1 | 规则解释去开发史；统计清洗合并 |
| v0.6.1/v0.6.2 | 切分单引号 bug；死代码清零；YAML 折叠空格清洗 |

## 7. 已知限制

- 摘要级短文本：统计指标样本不足时静默不判（不报"数据不足"是设计——报告里 `—` 已说明）
- 公文体/新闻体：天然工整，句长 CV 低是真人文风，现阈值会误伤——分档阈值在 P2
- 词表特征随模型漂移：英文侧 delve（GPT-5 后骤降）、破折号（GPT-5.1 压制）已证明静态词表会过期；漂移监测机制已上线（v0.17 `tools/drift_monitor.py`），era 季度重挖在 P3
- C-ReD 摘要语料测不到"首先…其次"等展开型套路（摘要太短），这些规则的区分度数字待完整论文语料补充
- 评分的长度语义：字级 2-gram TTR 在数百字以上趋饱和（≈0.92），polished 长文指数偏高——长档（≥600 字）分档系数已上线（v0.12.0，长档 holdout 0.975），但校准人群仍是摘要级语料，真实长文真人样本待积累；毕设实测（2.9 万字，指数 92→99）为该边界实例
- official 评分的**文种边界**（v0.18.1 泛化体检实证）：系数对事务公文（通知/通报/方案正文）有效，对省级门户"印发类"（正文=规划/方案全文附录）与"批复"不可靠——真人规划全文的指标密度/低 ngram 比 AI 默认公文更"AI"，印发类同文种 AUROC 0.294 反转；此类文本 v0.19.0 起带"文种域外"提示、v0.20.0 起**直接不出指数**（`genre_scoring` 抑制，测量数字见 rules.md §8）——报告保留规则发现与统计，只有综合分缺位；未来跨省配对语料可按双道判定线（分层 CV + LOCO ≥ 0.873）重新申报文种系数

## 8. 开源边界（永不混流）

入库：代码、规则库 YAML、文档、评测脚本与评测结果（_qa/*.md|json）、测试 fixture（自写样本）。
不入库：下载的第三方语料（_qa/corpus/，版权与体积）、用户的论文与报告、私人语料（corpus_private/）、API key 与本地配置。
