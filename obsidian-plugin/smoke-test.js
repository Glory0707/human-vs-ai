/* Obsidian 插件冒烟测试(node 直跑,不启 Obsidian)。
 *
 * 覆盖可测核心:产物完整性(manifest/versions/占位符清空)、规则与评分注入、
 * 引擎分析、报告/建议 HTML 与 Markdown 导出、makePlugin 的 obsidian 桩装配。
 * 视图生命周期是 Obsidian 宿主职责,手动验收步骤见 docs/plan.md。
 *
 * 运行:node obsidian-plugin/smoke-test.js
 * 产物:_qa/obsidian-preview.html(报告预览,浏览器可直接打开做视觉审查)
 */
const fs = require("fs");
const path = require("path");

const PLUGIN = __dirname;
const ROOT = path.join(PLUGIN, "..");
let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`[ok] ${name}`);
  else { failed++; console.log(`[FAIL] ${name}${extra ? " — " + extra : ""}`); }
}

// 1. 产物完整性
const mainJs = fs.readFileSync(path.join(PLUGIN, "main.js"), "utf-8");
const manifest = JSON.parse(fs.readFileSync(path.join(PLUGIN, "manifest.json"), "utf-8"));
const versions = JSON.parse(fs.readFileSync(path.join(PLUGIN, "versions.json"), "utf-8"));
const pyVersion = (fs.readFileSync(path.join(ROOT, "human_vs_ai", "__init__.py"), "utf-8").match(/__version__ = "([^"]+)"/) || [])[1];
check("manifest id", manifest.id === "human-vs-ai");
check("manifest version matches package", manifest.version === pyVersion, `${manifest.version} vs ${pyVersion}`);
check("versions.json covers current", versions[manifest.version] === manifest.minAppVersion);
check("no leftover placeholders", !/__RULES_JSON__|__SCORING_JSON__|__ENGINE__|__REWRITE__|__RENDER__|__VERSION__/.test(mainJs));
check("styles.css present", fs.existsSync(path.join(PLUGIN, "styles.css")));

// 2. 加载 main.js（纯函数部分不需要 obsidian）
const mod = require(path.join(PLUGIN, "main.js"));
check("profiles injected", ["academic", "essay", "general", "news", "official", "personal", "review"]
  .every(p => mod.PROFILES.includes(p)));

const HvA = require(path.join(ROOT, "web", "engine.js"));
const RULES = Object.fromEntries(mod.PROFILES.map(p => [p, null]));
// 规则从 main.js 不可直接取（内联于闭包）——用分析行为反推注入成功
const aiText = fs.readFileSync(path.join(ROOT, "tests/data/ai_academic.txt"), "utf-8");

// makePlugin 用 obsidian 桩装配，并从中取一次真实分析所需的闭包——
// 为让冒烟能跑分析，main.js 导出了 renderReportHtml；分析走 engine + 注入规则，
// 这里通过 makePlugin 的 HvAView.analyze 不可行（宿主职责），改为：
// 用 web/engine + web/rules 等价性由 check_web_consistency 守护，此处验证
// main.js 内联规则与分析路径是否一致——通过 makePlugin 桩拿到 HvAView 原型。
const obsidianStub = {
  Plugin: class { constructor() {} async loadData() { return {}; } async saveData() {} },
  ItemView: class { constructor() { this.contentEl = { empty() {}, addClass() {}, innerHTML: "", querySelector: () => null, querySelectorAll: () => [] }; } },
  MarkdownView: class {},
  Notice: class { constructor(msg) { this.msg = msg; } },
  PluginSettingTab: class {},
};
const made = mod.makePlugin(obsidianStub);
check("makePlugin assembles", !!made.HvAPlugin && !!made.HvAView && !!made.HvASettingTab);

// 3. 分析与导出（规则经 main.js 闭包注入——用 HvAView.analyze 的行为验证不可行，
//    改用与 build 相同的规则源做行为等价性抽查：报告 HTML 含规则 ID 与免责）
const EXT_RULES = require(path.join(ROOT, "vscode-extension", "rules.json"));
const EXT_SCORING = require(path.join(ROOT, "vscode-extension", "scoring.json"));
const result = HvA.analyze(aiText, EXT_RULES.academic, EXT_SCORING.academic || null);
const html = mod.renderReportHtml("academic", result);
check("report html has rule ids", html.includes("L-FORM-01"));
check("report html has disclaimer", html.includes("不是 AI 判定"));
check("report html has seal band", html.includes('class="seal ') && html.includes("AI味指数"));
const md = mod.reportToMarkdown("academic", result);
check("markdown has stats", md.includes("## 全文统计"));
check("markdown has disclaimer", md.includes("不是 AI 判定"));

// 3.5 文种域外提示（v0.19.0）：HTML 视图与 Markdown 导出都带提示行
const YINFA_TEXT = "各街道办事处，区政府各部门、各直属单位：《某区口袋公园建设三年行动计划（2026—2028年）》已经区政府同意，现印发给你们，请结合实际认真组织实施。为完善城市绿色空间布局，结合我区实际，制定本行动计划。一、总体目标。到二〇二八年，全区建成口袋公园六十处，人均公园绿地面积明显提升。二、重点任务。优先利用边角地、桥下空间，见缝插绿，突出地域文化特色，一园一主题。三、保障措施。区绿化部门统筹推进，各街道落实属地责任，每月报送建设进展。";
const yinfaResult = HvA.analyze(YINFA_TEXT, EXT_RULES.official, EXT_SCORING.official || null);
check("genre ood flagged", yinfaResult.ood.indexOf("issuance-notice") >= 0,
  JSON.stringify(yinfaResult.ood));
const yinfaHtml = mod.renderReportHtml("official", yinfaResult);
check("genre line in report html", yinfaHtml.includes("文种域外") && yinfaHtml.includes("印发类"));
const yinfaMd = mod.reportToMarkdown("official", yinfaResult);
check("genre line in markdown", yinfaMd.includes("- ※ 文种域外（印发类）：系数按事务公文校准，指数仅供参考"));

// 4. 改写建议（personal）
const HvARewrite = require(path.join(ROOT, "web", "rewrite.js"));
const personalRules = require(path.join(ROOT, "vscode-extension", "rules.json")).personal;
const advice = HvARewrite.rewriteText("点击右上角选择文件，支持批量导入。\n别急，代码明天还在仓库里。", personalRules);
const adviceHtml = mod.renderAdviceHtml(advice);
check("advice html has tags", adviceHtml.includes("删") && adviceHtml.includes("改"));
const adviceMd = mod.adviceToMarkdown(advice);
check("advice markdown ok", adviceMd.includes("共"));

// 5. 预览页（视觉审查用）：亮暗两份
const preview = `<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="../obsidian-plugin/styles.css">
<style>body{margin:0;display:grid} .wrap{padding:16px} .wrap.dark{background:#1e1e1e}</style>
</head><body>
<div class="wrap"><div class="hva-root">${html}</div></div>
<div class="wrap dark"><div class="theme-dark hva-root">${html}</div></div>
</body></html>`;
fs.writeFileSync(path.join(ROOT, "_qa", "obsidian-preview.html"), preview);

if (failed) { console.log(`\n${failed} 项失败`); process.exit(1); }
console.log(`\n预览已写 ${path.join(ROOT, "_qa", "obsidian-preview.html")}`);
