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
const { renderReportHtml } = require(path.join(EXT, "extension.js"));
const HvA = require(path.join(EXT, "engine.js"));
const RULES = require(path.join(EXT, "rules.json"));

let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`[ok] ${name}`);
  else { failed++; console.log(`[FAIL] ${name}${extra ? " — " + extra : ""}`); }
}

// 1. 三个 profile 的规则都注入了
check("profiles injected", ["academic", "general", "official"].every(p => Array.isArray(RULES[p]) && RULES[p].length));

// 2. AI 学术 fixture:academic 下有命中,报告含规则 ID 与免责
const aiText = fs.readFileSync(path.join(ROOT, "tests/data/ai_academic.txt"), "utf-8");
const aiResult = HvA.analyze(aiText, RULES.academic);
check("ai fixture has findings", aiResult.findings.length >= 5, `got ${aiResult.findings.length}`);
const aiHtml = renderReportHtml("ai_academic.txt", "academic", aiResult, "testnonce");
check("html contains rule ids", aiHtml.includes("L-FORM-01"));
check("html contains disclaimer", aiHtml.includes("不是 AI 生成判定"));

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
const emptyHtml = renderReportHtml("empty.txt", "academic", emptyResult, "n");
check("empty report renders", emptyHtml.includes("未发现明显"));

// 5. 输出预览文件(视觉审查用)
const previewText = fs.readFileSync(path.join(ROOT, "tests/data/ai_official.txt"), "utf-8");
const previewResult = HvA.analyze(previewText, RULES.official);
const previewHtml = renderReportHtml("ai_official.txt", "official", previewResult, "n");
const out = path.join(ROOT, "_qa", "vscode-preview.html");
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, previewHtml, "utf-8");
console.log(`预览已写 ${out}`);

process.exit(failed ? 1 : 0);
