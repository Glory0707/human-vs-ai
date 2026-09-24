# human-vs-ai

**human-vs-ai** 是一个可解释的中文 AI 味分析器。它不做"AI 检测"，做两件别家没做的事：

1. **逐句指出哪里像模板**——每一处命中都带规则名、语言学解释、修改方向，全部规则公开可查（`human-vs-ai explain <ID>`）
2. **用真实语料校准过的统计指标**——句长节奏、连接词密度、4-gram 重复，阈值不是拍脑袋，是在 C-ReD 多文体语料（作文/新闻/问答/学术，真人 vs 9 个当代模型）和 HC3-Chinese 上标定的

纯本地运行，零网络调用，无账号，文本不上传。CLI 一条命令出报告。

## 它不是什么

**不是 AI 生成概率检测器。** 不输出"AI 率 87%"这类数字——那个数字既造不出来也不该被造出来：OpenAI 因"低准确率"下架了自家检测器，斯坦福实测 7 款主流检测器把中国考生真人托福作文平均误判 61%，Nature 2026 年报道《独立宣言》被判 95-100% AI。检测器测的是文风不是作者。

human-vs-ai 只回答三个问题：**这句话像模板吗？为什么像？往哪个方向改？** 命中≠AI——人类同样会写"首先…其次…"，单独任何一条都不构成证据。报告每次输出都带着这行免责。

## 安装与使用

```bash
pip install .            # 唯一硬依赖 PyYAML；Python ≥3.10
human-vs-ai check 论文.md            # 终端报告
human-vs-ai check 论文.md -f md -o 报告.md
human-vs-ai check 论文.md -f json    # 机器可读（接 CI / 编辑器插件）
human-vs-ai check 论文.md -f html -o 报告.html   # 可分享的静态报告页（内联样式，可打印）
human-vs-ai check 论文.md -f sarif   # SARIF 2.1.0（GitHub code scanning 直接可吃）
human-vs-ai check docs/              # 批量扫描目录/glob：按指数排序的汇总表
human-vs-ai check docs/ -f csv       # 批量汇总出 csv/json
human-vs-ai check 论文.md --fail-above 60        # 指数超阈值退出码 1（CI 门禁）
human-vs-ai diff 旧.md 新.md          # 改前改后对比：哪几类消了、指数往哪走
cat 论文.md | human-vs-ai check -    # 管道输入（check/stats/rewrite 均支持）
human-vs-ai stats 论文.md            # 只看统计特征（JSON）
human-vs-ai explain L-INFL-01        # 查一条规则的完整解释与出处
human-vs-ai rewrite 文案.txt         # 按个人口味给逐句改写建议（删/改/保留）
human-vs-ai profiles                 # academic（学术）· essay（作文）· general（问答/自媒体）· news（新闻）
                                     # · official（公文）· personal（个人口味）· review（短评）
human-vs-ai collect 稿件.md --label fp  # 导出脱敏校准样本（误报/漏报/准确，自愿提交）
human-vs-ai ppl 文案.txt               # 句级困惑度（可选：pip install 'human-vs-ai[ppl]' + HF 模型权重）
```

输入支持 txt / md（UTF-8、GB18030 自动识别）/ **docx / odt**（纯标准库解包，零新增依赖）。`--fail-above` 对 <8 句的未出分文件不判定（宁可不判，不假过）。

**网页版**：双击 [web/index.html](web/index.html)，浏览器打开即用，纯本地可离线，规则与 CLI 完全一致（双引擎一致性测试逐字段守护）。指数以印章呈现、命中词波浪线圈划、发现以批注卡列出；手动亮/暗切换（记忆选择，默认跟系统）。改了规则用 `python tools/build_web.py` 重新生成。

**VS Code 扩展**：把 [vscode-extension/](vscode-extension/) 目录放进 `%USERPROFILE%\.vscode\extensions\` 重载窗口，命令面板执行「human-vs-ai: 分析当前文档」出完整报告并在正文给命中句画严重级波浪线；「human-vs-ai: 改写建议（个人口味）」给删/改/留建议。面板跟随编辑器主题。构建产物已入库，clone 即用；改了规则用 `python tools/build_vscode.py` 重新注入。

**Obsidian 插件**：把 [obsidian-plugin/](obsidian-plugin/) 目录复制到 `<仓库>/.obsidian/plugins/human-vs-ai/`（文件夹名必须是 human-vs-ai），启用插件后：左侧栏印章图标或命令「分析当前文档」在侧边视图出报告（场景下拉/检测与改写建议切换/复制 Markdown），笔记修改 800ms 后自动重析；「改写建议（个人口味）」给删/改/留建议；设置页可选默认场景。视图跟随 Obsidian 亮暗主题。构建产物已入库，clone 即用；改了规则用 `python tools/build_obsidian.py` 重新生成。

**七个场景词表**：academic（学术）/ general（问答）/ official（公文）/ personal（口味）/ essay（作文，留出 0.951）/ news（新闻，留出 0.935）/ review（影评短评——统计无样本，仅词表层，诚实标注）。每库的砍掉清单与反向规则见 docs/rules.md。

**域外提示**：文本超出校准域时四端随行提示——文言/诗行为"文体域外，指数仅供参考"；official 场景的印发/批复类公文为"文种域外"且不出指数（详见已知限制）。

**段落热度**：人改 AI 初稿的混写文本里全篇一个分数必然失真——报告标出"哪几段最像 AI"（每段命中密度/句，前 3 段）。

**贡献校准样本（collect）**：`human-vs-ai collect 稿件.md --label miss|fp|hit` 导出脱敏 JSONL（手机号/邮箱/证件/卡号自动打码，附判定快照），自愿提交到项目渠道，帮词表在真实文本上进化。网页版报告栏「匿名样本」按钮同款。样本进料后用 `tools/drift_monitor.py` 按月看分布漂移（p50/规则命中率变化即触发词表复审）。

**口味校准层（personal）**：六个公开场景校准通用 AI 味；`personal` 校准的是作者本人的文案取舍——私库标注链（被毙 31 vs 定稿 37）归纳出 12 条口味条目，配套 `rewrite` 子命令。详见 [docs/taste_zhouao.md](docs/taste_zhouao.md)，语料永不入库。

## 功能总览

### 分析引擎

- **中文切分**：句末标点切句（引号内不算边界）、空行分段；标题与代码块整块剥离，**列表项与表格行内容照常分析**（问答/自媒体大量用列表写正文，整段丢弃会漏检），裸链接/邮箱剥离不污染字数——格式伪影混进统计只会污染指标（CCL 2025 实测：格式标记让检测指标虚高 23.66%）
- **三层规则**：词表层（模板连接词、意义拔高、无证据强化词、公式化开头/展望尾、政策腔大词等）→ 句式/形状层（首先其次套路、独句总结段）→ 统计层（句长 CV、连接词密度、段长一致性、4-gram 重复、破折号密度）
- **七场景规则库**：academic（C-ReD 校准）、general（知乎真实长回答 + gen2026 当季模型重拟合）、essay/news（C-ReD 全量类平衡拟合）、official（真人事务公文 71 篇 vs 当季模型公文 70 篇拟合，留出 0.923）、personal（私库标注链校准，被毙稿召回 31/31）、review（短评，仅词表层如实标注）。实测证明词表区分度强依赖文体（学术套话在问答语料区分度≈0；三连排比在学术摘要反向、在问答 +0.22；公文"切实/进一步"是正体词只能做密度统计），所以词表按 profile 分治、统计底盘共用
- **共现加权**：真人也会写的弱句式（单个连接词、单处否定式排比）单独命中只进弱命中参考区，全文 ≥2 处才升为正式发现——这是 linter 与"误判机器"的分界线
- **短文本统计保护**：全文统计判定最少 8 句（宁可不判，不给小样本数字）；标题与代码块跳过，格式伪影不污染统计

### 报告

- **AI 味指数（综合评分）**：报告第一行给出 0-100 整体分——规则命中密度与全文统计的逻辑回归合成，系数公开在规则库 YAML、每个特征贡献可拆解，分档锚定校准语料的真人分位（p50/p90）。它是风格综合分，**不是 AI 概率**。各场景分层留出验证见下表；长文（≥600 字）启用分档系数后 0.975；消融对照显示"纯扣词"在问答语料只有 0.577（≈瞎猜）——综合判断的价值正是这个差距。短文本（<8 句）不出分；personal/review 无评分语料不出分（宁缺毋滥），报告注明"未校准"
- **报告结构**：指数+构成 → 统计摘要（规模/节奏/词汇）→ 逐句发现（同句多规则聚合，不重复贴原句；命中词高亮）→ 弱命中（长文只列前 12 处）→ 一行免责
- **三种格式同一份内容**：终端（ANSI 彩色）/ Markdown / JSON（事实源永不截断）

### 实测区分度（校准语料，非宣传数字）

| 语料 | 指标 | AI | 真人 | AUROC |
|---|---|---|---|---|
| C-ReD 论文摘要（真人 80 vs 四模型 320） | 词表规则句均命中 | 0.254–0.407 | 0.051 | **0.888** |
| C-ReD 论文摘要 | 句长 CV | 0.274–0.383 | 0.483 | **0.799** |
| HC3-Chinese 问答（391 篇过 8 句门槛） | TTR（字级 2-gram MATTR） | 0.826 | 0.868 | 0.684 |
| HC3-Chinese 问答（general 词表） | 三连排比命中率 | 43% | 22% | +0.22 |
| 中国政府网公开公文 15 篇（official 词表） | 真公文误报率 | — | **0/15 = 0%** | 验收 <20% PASS |
| C-ReD composition（高考作文真人 1070 vs 7 模型 7544） | 综合评分 | AI | 真人 | **0.952**（留出 0.951） |
| C-ReD news（真人 1413 vs 7 模型 15112） | 综合评分 | AI | 真人 | **0.938**（留出 0.935） |
| gen2026 当季公文（真人事务公文 71 vs 9 模型 70） | 综合评分 | AI | 真人 | **0.957**（留出 0.923） |
| 知乎真实长回答 110 vs gen2026 当季回答 69（general 重拟合） | 综合评分 | AI | 真人 | **0.945**（留出 0.935；旧 HC3 系数在同期语料仅 0.602） |
| **样本外体检**：豆瓣/果壳真人 78 vs 未参拟合的四模型 27（general） | 综合评分 | AI | 真人 | **0.923**（冻结系数只测不调，掉幅 0.012 → PASS） |
| 样本外体检：湖北/四川公文真人 44 vs 四模型 27（official） | 综合评分 | AI | 真人 | 0.731（**文种边界**：系数绑定事务公文，印发全文附录/批复类真人侧大面积误报，见 `_qa/generalization-check.md`） |
| 文种切片判定：四省门户真人 75 vs 九模型 84（official，v0.20.0） | 综合评分 | AI | 真人 | 印发 0.464 / 批复 0.791（判定线 0.873）→ **两文种出分抑制**；文种内校准分层 CV 0.895 但渠道留一 0.448（渠道指纹），见 `_qa/genre-check.md` |
| 事务文种样本外：五省门户真人 35 vs 四模型 25（official，v0.22.0） | 综合评分 | AI | 真人 | **0.888**（冻结系数，掉幅 0.035 → PASS；抑制上线前同口径仅 11 篇 0.873 贴线），见 `_qa/generalization-check.md` |

句长 CV 是对四个模型（含最难检的推理模型 deepseek-r1）一致有效的唯一指标；CV 阈值按长度三档（<300 字 0.30 / <600 字 0.33 / 更长 0.37）。general 词表的互动尾巴、万能开场是当代特征，2023 语料测不到——诚实标注，不造数字。完整校准表与被证伪删除的规则见 [docs/rules.md](docs/rules.md)。

## 设计原则（三条底线）

1. **推断不是判定**——命中只说明出现了某种高频写作模式；报告措辞永远是风格提示
2. **弱规则必须共现**——单一迹象不构成证据（维基 Signs of AI writing 开篇第一句）；`low` 级规则孤立命中自动降级
3. **规则必须过评测**——每条规则带着区分度数字活在规则库里；为零或反向的删掉，写进砍掉清单防复活

## 为什么做

商业工具全是"测 AI 率 + 一键降 AI 率"的黑盒闭环：给分数不给解释，改写就是同义词替换。学生被误判后最想要的东西恰恰是解释——央视采访北邮学生原话："我希望 AIGC 检测可以告诉我，这段它是为什么判定我的 AIGC 率高，而不是只告诉我百分之多少。"开源侧则相反：humanizer 类改写 Skill 有 11k star，但零检测能力，改完只能付费去知网复检。human-vs-ai 补的是中间这层：**可解释、可校准、可本地运行的写作风格归因**。

## 已知限制

- 统计指标需要**足够文本**：句长 CV 至少 3 句才有意义，一段话的分析只看词表命中
- 阈值按**摘要与问答语料**校准；长度分档已上线（CV 三档阈值、评分 ≥600 字长档系数）；general/official 评分已完成当代语料重拟合（v0.17.8），评分类语料仍会随模型换代持续积累
- general 评分通过样本外体检（换模型+换渠道，0.923）；official 评分**绑定事务文种**——"印发《规划》全文附录""批复"类省级门户文种不出指数（v0.20.0 判定线定案：印发同文种 AUROC 0.464 无信号；批复文种内校准被渠道指纹污染，渠道留一仅 0.448），报告保留规则发现与文种域外提示行（判据与判定线见 docs/rules.md §8）
- 词表规则面向**当代模型文风**，会随模型版本漂移（delve 在 GPT-5 后骤降、破折号在 GPT-5.1 被官方压制）；漂移监测已上线（`tools/drift_monitor.py`），季度重挖用 `tools/era_remine.py`（C-ReD 各模型子集挖新指纹 + 现役词表体检，报告见 `_qa/era-remine-*.md`）
- 词汇丰富度（TTR）用**字级 2-gram 口径**（与网页/插件端逐位一致）；不做词级切分
- 句级困惑度是**可选弱信号**：doc 级区分度 0.6B 模型 0.633、1.7B 模型 0.657——规模 ×2.8 仅 +0.024，对规模响应平缓，放大不现实（`_qa/ppl-calibration.md`）；故默认关闭、不进评分，仅 `ppl` 子命令显式调用
- 本工具**不能**用于证明或豁免任何"AI 代写"指控——它没有这个能力，也不该有

## 开发

```bash
python -m pytest tests/ -q              # 181 项单元+边界+评分+口味+格式+多文体+域外+模糊回归（私库层缺语料自动跳过）
python tools/check_web_consistency.py   # Python/JS 双引擎一致性 322 项 × 7 场景（需 node）
python _qa/drift_battery.py             # Py/JS 53 探针对抗对拍（跑完自清理）
node vscode-extension/smoke-test.js     # VS Code 扩展冒烟 32 项
node obsidian-plugin/smoke-test.js      # Obsidian 插件冒烟 19 项
python tools/build_web.py               # 重新生成网页单文件
python tools/build_vscode.py            # 重新注入扩展规则与引擎
python tools/evaluate_cred.py           # C-ReD 学术语料评测（语料下载见 docs/rules.md）
python tools/evaluate.py                # HC3-Chinese 问答语料评测
python tools/evaluate_official.py       # 公文语料评测（误报率验收）
python tools/eval_taste_regression.py   # 口味回归（需私库语料，缺失自动跳过）
python tools/gen_samples.py --scene qa  # 大模型 API 生成当季评测语料（key 外读，语料不入库）
python tools/adversarial_eval.py        # 对抗自评测：改写器/LLM 当攻击者
python tools/oos_check.py               # 泛化体检：冻结系数跑样本外语料（语料见 _qa/corpus/）
python tools/genre_check.py             # 文种切片判定：冻结 AUROC + 分层 CV + 渠道 LOCO
python tools/scrape_genre_corpus.py     # 抓省门户印发/批复真人语料（文种校准用，不入库）
python tools/drift_monitor.py --input corpus_private/*.jsonl  # collect 样本漂移监测
python tools/era_remine.py             # C-ReD 词频重挖：新指纹候选 + 现役词表体检（报告 _qa/era-remine-*.md）
python tools/ppl_calibration.py        # 句级困惑度标定（需 torch/transformers + 本地 HF 权重，见 _qa/ppl-calibration.md）
python tools/pkg_check.py              # PyPI 发布自查：构建+产物内容审计+twine+干净 venv 安装冒烟
```

推送后确认 CI 绿（`gh run list --limit 1`）——CI 含构建产物新鲜度守护，
重新生成后与入库产物零 diff 才放行。

设计文档：[design.md](docs/design.md)（定位与取舍）· [rules.md](docs/rules.md)（规则库与校准）· [plan.md](docs/plan.md)（计划与走查）· [中文AI味领域深度研究.md](docs/中文AI味领域深度研究.md)（立项调研）。

## License

[MIT](LICENSE)。
