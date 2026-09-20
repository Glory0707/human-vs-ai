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
const { renderReportHtml, renderAdviceHtml } = ext;
const HvA = require(path.join(EXT, "engine.js"));
const HvARewrite = require(path.join(EXT, "rewrite.js"));
const RULES = require(path.join(EXT, "rules.json"));

let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`[ok] ${name}`);
  else { failed++; console.log(`[FAIL] ${name}${extra ? " — " + extra : ""}`); }
}

// 1. 四个 profile 的规则都注入了
check("profiles injected", ["academic", "general", "official", "personal"].every(p => Array.isArray(RULES[p]) && RULES[p].length));

// 2. AI 学术 fixture:academic 下有命中,报告含规则 ID 与免责
const aiText = fs.readFileSync(path.join(ROOT, "tests/data/ai_academic.txt"), "utf-8");
const aiResult = HvA.analyze(aiText, RULES.academic);
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
check("empty report renders", emptyHtml.includes("未发现明显"));
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

// 6. 输出预览文件(视觉审查用)
const previewText = fs.readFileSync(path.join(ROOT, "tests/data/ai_official.txt"), "utf-8");
const previewResult = HvA.analyze(previewText, RULES.official);
const previewHtml = renderReportHtml("ai_official.txt", "official", previewResult);
const out = path.join(ROOT, "_qa", "vscode-preview.html");
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, previewHtml, "utf-8");
console.log(`预览已写 ${out}`);

process.exit(failed ? 1 : 0);
