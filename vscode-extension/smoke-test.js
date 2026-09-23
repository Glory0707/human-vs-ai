/* VS Code 扩展冒烟测试(node 直跑,不启 VS Code)。
 *
 * 覆盖扩展的可测核心:规则加载、引擎分析、报告 HTML 生成——
 * activate/WebviewPanel 是 VS Code 宿主职责,手动验收步骤见 docs/plan.md T5.1。
 *
 * 运行:node vscode-extension/smoke-test.js
 * 产物:_qa/vscode-preview.html(报告预览,浏览器可直接打开做视觉审查)
 */
const fs = require("fs");
const path = require("path");

const EXT = __dirname;
const ROOT = path.join(EXT, "..");
const ext = require(path.join(EXT, "extension.js"));
const { renderReportHtml, renderAdviceHtml, locateFindings } = ext;
const HvA = require(path.join(EXT, "engine.js"));
const HvARewrite = require(path.join(EXT, "rewrite.js"));
const RULES = require(path.join(EXT, "rules.json"));
const SCORING = require(path.join(EXT, "scoring.json"));

let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`[ok] ${name}`);
  else { failed++; console.log(`[FAIL] ${name}${extra ? " — " + extra : ""}`); }
}

// 1. 七个 profile 的规则都注入了；评分模型只注入到已校准的 profile
check("profiles injected", ["academic", "essay", "general", "news", "official", "personal", "review"].every(p => Array.isArray(RULES[p]) && RULES[p].length));
check("scoring injected for calibrated profiles", !!SCORING.academic && !!SCORING.general && !!SCORING.essay && !!SCORING.news && !!SCORING.official && !SCORING.personal && !SCORING.review);

// 2. AI 学术 fixture:academic 下有命中,报告含规则 ID 与免责
const aiText = fs.readFileSync(path.join(ROOT, "tests/data/ai_academic.txt"), "utf-8");
const aiResult = HvA.analyze(aiText, RULES.academic, SCORING.academic || null);
check("ai fixture has findings", aiResult.findings.length >= 5, `got ${aiResult.findings.length}`);
const aiHtml = renderReportHtml("ai_academic.txt", "academic", aiResult);
check("html contains rule ids", aiHtml.includes("L-FORM-01"));
check("html contains disclaimer", aiHtml.includes("不是 AI 判定"));
// 同一规则的解释全文只讲一次(与 CLI/网页口径一致)
const lconnHits = aiResult.findings.filter(f => f.rule_id === "L-CONN-01").length;
const lconnExplained = aiHtml.split("这批词本身没有错").length - 1;
check("rule explanation deduped", lconnHits >= 2 ? lconnExplained === 1 : true,
  `hits=${lconnHits} explanations=${lconnExplained}`);

// 3. 词表分治:official 的 O-* 词表规则对学术人类 fixture 零句级命中
//    (统计底盘共用,D-PARA/D-DASH 对均匀段落照常提示是预期行为)
const humanText = fs.readFileSync(path.join(ROOT, "tests/data/human_academic.txt"), "utf-8");
const humanResult = HvA.analyze(humanText, RULES.official);
const oHits = humanResult.findings.filter(f => f.rule_id.startsWith("O-"));
check("official lexicon silent on academic human text", oHits.length === 0,
  `got ${JSON.stringify(oHits.map(f => f.rule_id))}`);

// 4. 空/极短输入不炸
const emptyResult = HvA.analyze("", RULES.academic);
check("empty input safe", Array.isArray(emptyResult.findings));
const emptyHtml = renderReportHtml("empty.txt", "academic", emptyResult);
check("empty report renders", emptyHtml.includes("未发现模板化写作"));
// 场景标签中文化
check("profile label localized", renderReportHtml("x.txt", "official", emptyResult).includes("公文"));

// 5. 改写建议面板（personal）：删/改/留三档与页脚
const copyText = ["别急，代码明天还在仓库里。", "实测三次，失败率降到 3%。",
                  "自动匹配相关段落，一次最多三条。"].join("\n");
const advice = HvARewrite.rewriteText(copyText, RULES.personal);
const adviceHtml = renderAdviceHtml("copy.txt", advice);
check("advice has counts", adviceHtml.includes("共 <b>3</b> 条"));
check("advice has del tag", /class="advice del"/.test(adviceHtml));
check("advice footer", adviceHtml.includes("梗得人来补"));
check("advice data kept", adviceHtml.includes("失败率降到 3%"));

// 6. 原句命中高亮：<mark> 包住命中片段，句子不再截断（剥掉标签后应含完整原句）
check("match highlighted", aiHtml.includes("<mark>"));
const plain = aiHtml.replace(/<[^>]+>/g, "");
check("sentence not truncated", aiResult.findings.some(f => f.sentence && plain.includes(f.sentence)));

// 7. 弱命中截断：>12 条只列 12 条并给"…等 N 处"尾注
const mkHint = i => ({ rule_id: `X-${i}`, rule_name: `弱规则${i}`, severity: "low",
                       para: i, sentence: "", matches: [], explanation: "", suggestion: "", taste: "" });
const manyHints = { findings: [], hints: Array.from({ length: 15 }, (_, i) => mkHint(i)),
                    stats: { n_paragraphs: 1, n_sentences: 1, n_chars: 10,
                             sentence_cv: NaN, para_len_cv: NaN, ttr: NaN,
                             conn_density: NaN, ngram_repeat: NaN } };
const capHtml = renderReportHtml("cap.txt", "academic", manyHints);
check("hints capped at 12", !capHtml.includes("列前") && capHtml.includes("…等 3 处"));

// 8. 主题适配：暗色类钩子与主题变量都在样式里
check("dark theme hooks", aiHtml.includes("vscode-dark") && aiHtml.includes("--vscode-editor-background"));

// 8.5 综合评分行：指数 + 构成 + 分档（academic 带 scoring 注入）
check("score row rendered", aiHtml.includes("AI味指数") && /class="seal (high|medium|low)"/.test(aiHtml),
  "index row missing");
check("score components shown", aiHtml.includes("构成"));
const officialResult = HvA.analyze("首先进行研究。其次进行分析。此外完成验证。与此同时记录数据。最后归纳结论。另外补充实验。总之效果良好。结果表明方法可行。", RULES.official, null);
const officialNoScoreHtml = renderReportHtml("x.txt", "official", officialResult);
// 未校准档：无分档色分数，但给一行"为什么没分"；短文本连说明行也不出
check("no score band when uncalibrated", !/class="s-(high|medium|low)"/.test(officialNoScoreHtml) && officialNoScoreHtml.includes("该文体未校准评分"));
const officialShort = HvA.analyze("首先进行研究。其次进行分析。", RULES.official, null);
check("no score note on short text", officialShort.score_note === "" && !renderReportHtml("x.txt", "official", officialShort).includes("AI 味指数"));

// 9. activate 命令注册：vscode 模块桩加载扩展并触发 activate——
//    回归 v0.9.0 起的隐患（activate 内引用了未 require 的 vscode，激活即崩）
const Module = require("module");
const fakeVscode = {
  commands: {
    registered: [],
    registerCommand(id) { fakeVscode.commands.registered.push(id); },
  },
};
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "vscode") return fakeVscode;
  return origLoad.call(Module, request, parent, isMain);
};
try {
  const { activate } = require(path.join(EXT, "extension.js"));
  const subscriptions = [];
  activate({ subscriptions });
  check("activate registers commands", fakeVscode.commands.registered.length === 2 &&
    subscriptions.length === 2, `got ${fakeVscode.commands.registered.join(",") || "none"}`);
} catch (e) {
  check("activate registers commands", false, e.message);
} finally {
  Module._load = origLoad;
}

// 10. 发现→文档定位：顺序定位、重复句推进第二处、doc 级跳过、找不到的句子跳过
const doc = "# 报告\n\n首先要明确目标。其次要持续投入。\n\n- 首先要明确目标。\n- 其次要持续投入。\n";
const fakeFindings = [
  { para: 0, sentence: "首先要明确目标。", severity: "high" },       // 第一次出现
  { para: 1, sentence: "首先要明确目标。", severity: "medium" },     // 重复句：定位到第二次出现
  { para: 1, sentence: "*强调*过的句子被剥离后找不到原文", severity: "low" }, // 原文没有 → 跳过
  { para: -1, sentence: "全文级不该出现在装饰里", severity: "high" }, // doc 级跳过
];
const located = locateFindings(doc, fakeFindings);
check("locate count", located.length === 2, `got ${located.length}`);
check("locate first", located[0].start === doc.indexOf("首先要明确目标。") && located[0].severity === "high");
check("locate duplicates advance", located.length === 2 && located[1].start > located[0].start,
  JSON.stringify(located));

// 6. 输出预览文件(视觉审查用):academic 带评分,印章可见
const previewText = fs.readFileSync(path.join(ROOT, "tests/data/ai_academic.txt"), "utf-8");
const previewResult = HvA.analyze(previewText, RULES.academic, SCORING.academic || null);
const previewHtml = renderReportHtml("ai_academic.txt", "academic", previewResult);
const out = path.join(ROOT, "_qa", "vscode-preview.html");
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, previewHtml, "utf-8");
console.log(`预览已写 ${out}`);

process.exit(failed ? 1 : 0);
