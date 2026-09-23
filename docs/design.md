# human-vs-ai 项目设计

> 当前版本见 human_vs_ai/__init__.py。已实现能力见 [README](../README.md)，本文只保留定位、原则、取舍与未竟事项。

---

## 1. 一句话定位

**本地优先、可解释的中文 AI 味分析器：逐句指出哪里像模板、为什么像、怎么改——不判 AI 概率。**

四个差异点：

1. **可解释** —— 每处命中带规则名、语言学解释、修改方向、研究出处；商业工具给分数不给解释，我们反过来。
2. **过评测的规则** —— 每条规则带着 C-ReD/HC3 上的区分度数字；证伪即删，砍掉清单防复活。
3. **本地优先** —— 零网络调用，文本不上传；唯一硬依赖 PyYAML。
4. **按场景组织** —— 词表规则分 profile（学术/问答/公文/口味），统计底盘共用；实测证明跨文体复用词表必然失效。

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

- **P2**：general 当代长文 QA **评分**语料积累（词表当代验证 v0.17 双代际完成；gen2026 问答 69 篇已入库 `_qa/corpus/`，评分拟合等样本扩量；样本入口为 collect 导出的匿名 JSONL）· official 事务公文真人样本扩充（v0.17 词表初步验证完成：9 模型 AI 公文组合覆盖 100% / 真人 0 误伤，但真人侧仅 15 篇规章文体）
- **P3**：规则 era 自动化挖掘（从 C-ReD 各模型子集季度重挖词频漂移；漂移监测机制 v0.17 已上线 `tools/drift_monitor.py`）· 句级困惑度（Qwen 本地小模型，可选插件不进默认依赖）· 观点反复解释检测（需语义相似度，n-gram 只能部分覆盖）

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

- **单元测试**：161 项（切分/统计/引擎/边界/报告与文案/评分/口味与改写/格式与工作流/多文体 profile/域外与漂移/端到端区分度）；私库回归 5 项无 corpus_private/ 时自动跳过
- **C-ReD paper 校准**（真人 80 vs deepseek-v3/qwen-3/gpt-4o/deepseek-r1 各 80）：词表句均命中真人 0.046 vs AI 0.170–0.307，AUROC **0.804**；句长 CV 真人 0.483 vs AI 0.274–0.383（四模型全低），AUROC **0.799**；deepseek-r1 最难检
- **HC3-Chinese 校准**：词表 AUROC 0.476（学术词表在问答文体失效——profile 分治的实证）；CV 0.763；字级 2-gram TTR 0.684
- **长度分档**：真人 CV p50 短/中/长 = 0.467/0.494/0.520，D-UNIF 三档阈值 0.30/0.33/0.37（数据 `_qa/length-tiers.md`）
- **公文**：真人公开公文误报 **0/15**（验收 <20% PASS）；AI 样本 7 处命中逐条人工核对成立
- **fixture 冒烟**：AI 样本 20 处命中（高 4）vs 人类样本 0 高 0 中（tests/data/）
- **多文体扩展（v0.14）**：essay 评分留出 **0.951**、news **0.935**（C-ReD 全量类平衡）；review 短评词表层不出分——详见 rules.md §9
- **当代验证（v0.17）**：essay 全量重跑 AUROC **0.942** / news **0.933**（按模型分解：qwen-2.5/claude/gpt-4o 召回 95%+，gpt-3.5 旧代仅 53%）；general 词表双代际验证（C-ReD QA 全量 + gen2026 当季 9 模型 69 篇：三连排比 83% 命中仍是当代最顽固指纹）；official 词表初步验证（9 模型 AI 公文组合覆盖 100% / 真人 0 误伤）；**域外探测**判据 C-ReD 全量 10.4 万篇校准（正样本 5/5、误报 0.005%）；**对抗自评测**（LLM 洗稿削词表 89% 后仍 93.3% 超阈值——统计底盘扛住定向规避）——详见 rules.md §9 与 _qa/*.md
- **引擎性能**：10 万字 112ms（线性，2026-09 基准）
- **双引擎一致性**：27 段语料 + 15 条改写探针 × 7 profile = **308 项**逐字段 diff 全绿（含 ood/para_heat 字段对拍）；含 emoji 码点/行分隔符全集/孤立低代理/闭引号吸收/未闭合围栏/双竖线表格/引号不配对/邮箱/括号洪水等对抗探针
- **口味校准层**：personal 12 条口味条目 + rewrite；被毙稿召回 31/31、定稿误报 0/37、改写维度 5/6；`tools/check_private_leak.py` 守护边界

### 轮次日志（细节见 [plan.md](plan.md) 轮次注记与 [rules.md](rules.md) §8）

| 轮次 | 要点 |
|---|---|
| v0.6.1/v0.6.2 | 切分单引号 bug 修复；死代码清零；YAML 折叠空格清洗；四端解释去重 |
| v0.7.1 | 文案与规则解释去开发史；统计清洗合并；规则解释数字矛盾修正 |
| v0.8.0 | 切分块模型（列表/表格入分析、URL/邮箱剥离）；personal 关怀腔泛化；网页中文化+改写模式；扩展 rewrite 命令 |
| v0.9.0 | MATTR 滚动窗口 O(n)；正则缓存；暗色模式；复制 Markdown；命中高亮；扩展波浪线装饰；stdin |
| v0.9.1 | 短文本只留规模行等三处减法 |
| v0.10.0 | 四臂消融驱动综合评分落地（消融数字见 rules.md §8） |
| v0.11.0 | jieba 退场、TTR 口径全文统一为字级 2-gram；general 评分类平衡（0.838/0.870，真人分位 27/69）；未校准档说明行；指数整数化 |
| v0.11.1 | 修扩展 activate 激活即崩 bug；web/render.js 共享渲染层；segment/engine 抽公共函数；load_rules 缓存；导出面裁剪 |
| v0.11.2 | 全部用户可见文案减负；目录清理 |
| v0.11.3 | 对拍 48 探针抓三类真 bug（JS 码点 vs 码点、空均值、EMPH/EMAIL 正则回溯 51.4s→0.11s）；CLI 干净报错；网页三处竞态修复；一致性 100→136 项 |
| v0.12.0 | 动效系统（入场淡入/呼吸提示/reduced-motion，逐键不闪）；评分长文分档（全量 4993 篇，长档 holdout 0.975 vs 全局 0.946，毕设实测暴露）；auroc 并列检测 NaN 死循环修复（分档拟合实证）；当代样本初测入 rules.md §5；CI 上线 |
| v0.12.1 | 研究工具去重：auroc 四份副本收敛为 evaluate_cred 单实现、逻辑回归收敛为 fit_score_tiers.fit；一次性诊断脚本删除、缓存重建并入 fit_tiers_compare |
| v0.12.2 | 目录与文案清理：删 VERSION/_server_deps 与死字段 human_ref；扩展 rules.json 剥离校准注（与网页对齐） |
| v0.12.3 | 三类双端漂移修复（孤立低代理/splitlines 行界全集/句尾闭引号吸收）；网页大文本 rAF 代数守卫；一致性 140→156 项 |
| v0.12.4 | 端到端+视觉审查：修重复句分组标题爆炸（web/md/CLI 三处去重）；11 测试点全过、控制台零错误 |
| v0.13.0 | 能力与格式扩展（v0.13）：docx/odt 输入（纯 stdlib 解包）；批量扫描（目录/glob→指数排序汇总表，csv/json）；diff 改进闭环（规则级已消除/新增/增减 + 指数/构成 delta）；--fail-above CI 门禁；SARIF 2.1.0 与 html 静态报告出口；网页端「文纸·朱批」设计语言重做（印章指数/批注卡/信纸横线/竖排铭文，超高分辨率视觉审查三轮实修：改写器空转候选、多句段落截半句候选、T12 校准比例笔误） |
| v0.13.1 | 网页端设计语言完全对照 eggpaper 重写（v0.13.1）：token 同源（暖墨白纸/发丝线/深青工作色+朱砂批改色/Fraunces 品牌字/mono 标签/弹簧缓动/阴影），segmented 滑块分段控件、56px 顶栏+分析流光、眉批式批注卡、命中改 b-warn 波浪线配方、空态=大徽记+铭文章、toast、手动亮/暗（html.dark + localStorage）、印章徽记陪伴交互（戳/三连戳翻滚/1/24 喷嚏/分析 busy 节拍）；移动端触控目标 24→44px（视觉审查闭环）；htreport 同步 token |
| v0.13.2 | Logo 重设计（无文字）：印章框内一行字迹——左半手写波浪（人）右半拉直（AI），接点切线水平；单色 currentColor 成立、16px 可读（缩放标尺 16→104px 验证 + 视觉验收）；徽记沿用波浪几何与报告命中线同源；favicon/顶栏/空态三处同步 |
| v0.14.0 | 多文体扩展：接入 C-ReD 五域语料（真人+9 当代模型，164MB），新增 news/essay/review 三 profile——news 留出 0.935、essay 留出 0.951（全量类平衡拟合），review 短评词表层不出分（中位 132 字过门槛 0.2%）；general 词表获当代验证（QA 域三连排比 0.17、收束词复现）；collect 脱敏校准样本导出（CLI+网页按钮）；词表挖掘/侦察/拟合工具三件套（mine_patterns/domain_recon/fit_domain）；多文体砍掉清单入 rules.md §9 |
| v0.14.1 | 交互优化：原稿/报告分隔线可拖（eggpaper rail-grip 同款：悬停青线/拖动全局 col-resize 禁选中/双击复位/方向键微调/localStorage 记忆，22-78% 限幅）；清空带一级撤销 toast；原稿栏头部实时字数·句数+不足 8 句提示；txt/md 拖稿入栏；空态「看个例子」合成样例；场景/模式记忆；segmented 滑块改按活动按钮真实几何定位（修复盖住邻项文字的缺陷） |
| v0.15.0 | Obsidian 插件：obsidian-plugin/（main.template.js + styles.css + 构建产物 main.js/manifest.json/versions.json），tools/build_obsidian.py 注入引擎/规则/评分（四端同一事实源），命令「分析当前文档/改写建议」+ 侧边视图（印章/批注卡/波浪线，亮暗跟随 Obsidian 主题）+ 笔记修改 800ms 防抖自动重析 + 设置页默认场景；冒烟 14 项（obsidian 桩装配 + 产物完整性 + 预览页） |
| v0.16.0 | 全端打磨轮：弱命中三端折叠（details，默认收起降噪）；VS Code webview 报告对齐 eggpaper 设计（印章+眉批卡+波浪线，修亮色档位变量缺失/CSP 拦 data 图两处真实缺陷）+ 场景 QuickPick（记住上次）；网页印章入场动画/筛选计数与记忆；移动端分段换行+填充高亮（修 7 项溢出）；引擎基准 10 万字 112ms 线性 |
| v0.16.1 | 冗余清理轮（零功能变化）：render.js 死叶 scoreRow 移除、sealHtml 三份拷贝收敛为共享叶子；死 CSS（.seal.none/旧 b.s-* 档位色）清理；pyflakes 清零（htreport OrderedDict、build_obsidian shutil、domain_recon re/hvastats、fit_score rng 与无占位 f-string、fit_domain 死赋值、report.py _TIER_LABEL）；pyflakes 纳入日常自查 |
| v0.16.2 | 文件与文案清理轮：删 .playwright-mcp/gui-test-screenshots 等中间文件；全端文案收短（拖动提示/样本导出悬浮与 toast/空态副题/铭文悬浮/QuickPick 占位/指数副行去掉与印章重复的"风格综合分"前缀），保留纯本地信任行与免责行 |
| v0.16.3 | 测试员轮（131 项测试）：修 4 个边界 bug——①batch 字面路径优先于 glob（文件名带 [ ] 被字符类吃掉误报"无匹配"）；②web 全局拦截文件拖放默认行为（拖到栏外浏览器整页跳转丢会话）；③VS Code QuickPick Esc 取消不再拿默认场景偷偷分析；④collect 空文本守卫。7 场景 × 19 组模糊轰炸（孤立代理/控制字符/纯标点/不平衡引号/超长行）0 炸 |
| v0.17.0 | 当代验证+域外+锚点轮：general 词表双代际当代验证（C-ReD QA 全量 + gen2026 当季 9 模型 69 篇，D-DASH 问答域反向砍掉）；域外文体探测器 ood.py + JS 同构（文言×低"的地得"×零"了"三信号、等长对句诗行，四端随行提示）；四端分数读数语言（"超过 90% 校准真人"，p50/p90 移入悬浮）；tools/gen_samples.py 大模型 API 语料生成器（key 外读） |
| v0.17.1 | 段落热度轮：compute_para_heat 每段加权密度（与全文 hit_density 同口径，level 三档），混写文本定位"哪几段最像 AI"，四端同行展示；density 保留全精度（Py banker's vs JS half-up 漂移规避） |
| v0.17.2 | 校准机制化轮：tools/adversarial_eval.py 对抗自评测（改写器/LLM 双攻击者）；tools/drift_monitor.py 漂移监测（collect 样本按 profile 聚合对比基线，p50≥15 分/规则≥10pp 信号）；official 场景 gen2026 公文 70 篇初步验证（组合覆盖 100%/真人 0 误伤） |
| v0.17.3 | T6 收尾：.stats .row 特异性覆盖 .ood-note 致暗色域外提示退化灰字——三端选择器提升（visual-judge 抓出）；对抗评测双攻击者合并报告（LLM 洗稿削词表 89%、93.3% 仍超阈值——统计底盘扛住定向规避）；10 状态高分辨率视觉走查 9 pass / 1 截图脚本失误 |
| v0.17.7 | 测试员轮（162 项测试）：修 2 个 bug——①collect.sanitize 卡号正则只认 16-19 位，22 位长数字串（订单号等）整段漏打码，改 16 位起整段打码（脱敏宁枉勿纵）+ 回归；②drift_monitor 目录输入抛 PermissionError traceback，抽 resolve_inputs 跳目录并干净报错 + 回归。排查无恙层：Py/JS 15 组对抗探针 ood/para_heat/excerpt 逐字段一致；CLI 黑盒 10 组边界（GB18030/二进制/空文件/不可写输出/不存在规则与场景/空 stdin）全过；web 竞态守卫（clearTimeout+analyzeGen 代际+双 rAF）与 Obsidian 同步 analyze（读当前活动文件，无错位）确认完备 |
| v0.17.6 | 文件与文案清理轮：删本地产物（.playwright-mcp/.pytest_cache/egg-info/__pycache__/_qa 截图与运行缓存，语料与私人数据不动）；文案收短——域外行"超出评测语料范围，指数与统计仅供参考"→"指数仅供参考"、热度行去"（命中密度/句）"括注（定位交互改悬浮提示）、collect 输出与 help 若干条收短、空态句号统一 |
| v0.17.5 | 冗余清理轮（零功能变化）：rewrite.py 死映射 _VOICE_RULES 删除（口味编号实际来自 personal.yaml 的 taste 字段）；render.js 纯内部叶子（compsHtml/OOD_NAME/SCORE_LABEL）移出导出表、vscode 幽灵解构 componentsText 移除；标点正则收敛单一事实源（stats.PUNCT，ood.py 删本地拷贝改导入——口径一致从人肉同步变结构保证）；ood.py 常量统一私有命名、drift_monitor NaN 判断收敛 finite()；test_ood 尾部 helper 归位 |
| v0.17.4 | 全端打磨轮：段落热度可点击——原稿自动选中对应段落首句（excerpt 定位，引擎 Py/JS 同构新增字段）；域外判定改全文+逐段聚合（白话引用文言段时全文统计被稀释致漏检，C-ReD 全量复测误报率不变）；构成列 HTML 版每项 nowrap 修手机端"标签 数值"拆行（visual-judge 抓出）；性能复测 7.1 万字 Py 140ms / JS 34ms 无回退，ood 正则预编译 |

## 7. 已知限制

- 摘要级短文本：统计指标样本不足时静默不判（不报"数据不足"是设计——报告里 `—` 已说明）
- 公文体/新闻体：天然工整，句长 CV 低是真人文风，现阈值会误伤——分档阈值在 P2
- 词表特征随模型漂移：英文侧 delve（GPT-5 后骤降）、破折号（GPT-5.1 压制）已证明静态词表会过期；漂移监测机制已上线（v0.17 `tools/drift_monitor.py`），era 季度重挖在 P3
- C-ReD 摘要语料测不到"首先…其次"等展开型套路（摘要太短），这些规则的区分度数字待完整论文语料补充
- 评分的长度语义：字级 2-gram TTR 在数百字以上趋饱和（≈0.92），polished 长文指数偏高——长档（≥600 字）分档系数已上线（v0.12.0，长档 holdout 0.975），但校准人群仍是摘要级语料，真实长文真人样本待积累；毕设实测（2.9 万字，指数 92→99）为该边界实例

## 8. 开源边界（永不混流）

入库：代码、规则库 YAML、文档、评测脚本与评测结果（_qa/*.md|json）、测试 fixture（自写样本）。
不入库：下载的第三方语料（_qa/corpus/，版权与体积）、用户的论文与报告、私人语料（corpus_private/）、API key 与本地配置。
